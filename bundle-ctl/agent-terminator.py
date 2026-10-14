#!/usr/bin/env python3
"""Open a terminator window split in two: the agent of a bundle, and a host shell in its folder.

    agent-terminator.py BUNDLE COMMAND...

For AGENT_TERMINAL of the bundle-ctl daemon, which appends COMMAND, the one that runs the agent
(agent-terminal.py BUNDLE). The same command with --shell runs the shell.

Terminator takes the commands of a split from a layout of its config only. So the window runs on a
copy of the user's config with that layout added: preferences saved from it go to the copy.

The title of the window says what the work is about (client/window-title.py), the title of each
pane where it runs. A pane split by hand has no title: the host .bashrc prints the same in any shell
that holds BUNDLE_CTL_AGENT_WINDOW, the bundle name. With wmctrl, the title of the window also
follows the status of the agent's session, in the sessions folder of the bundle's container, until
the window closes: it says the agent starts until its claude does, and that it failed when the
run ends before (.agent/done written since the window opened). Once its claude ends, the last mark
stays. When a VS Code window or another terminal takes the session over, agent-terminal.py closes
the window, its shell too: SIGUSR1 to BUNDLE_CTL_WINDOW_PID, this process. The window opens
minimized, so that a new agent does not take the screen: it waits in the window list
(window-picker.py).

An ops agent window (ops-SESSION) follows the record of that session in the host's sessions
folder, where the ops container writes, and the title of its conversation, which changes with no
change of status. Its mark goes once its claude ended, as the window list cannot tell which ops
window has an agent left. Opened with no task, it waits for Seb's first prompt: it shows in front.
"""

import ctypes
import ctypes.util
import json
import os
import pathlib
import shlex
import shutil
import signal
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from agents import OPS, get_agent_folder, get_mtime  # noqa: E402
from config import CLAUDE_CONFIG_CONTAINER  # noqa: E402

CHECK = 1
CLAUDE_CONFIG = pathlib.Path(CLAUDE_CONFIG_CONTAINER)
CONFIG_ROOT = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
HIDE_CHECK = 0.1
HOST_SESSIONS = pathlib.Path.home() / ".claude" / "sessions"
LAYOUT = "bundle-ctl agent"
LAYOUTS = "[layouts]"
PANES = """\
  [[{layout}]]
    [[[window]]]
      type = Window
      parent = ""
      order = 0
      maximised = True
    [[[panes]]]
      type = HPaned
      parent = window
      order = 0
      ratio = 0.6
    [[[agent]]]
      type = Terminal
      parent = panes
      order = 0
      profile = default
      title = "in the container: claude"
      command = "{agent}"
    [[[shell]]]
      type = Terminal
      parent = panes
      order = 1
      profile = default
      title = "on the host: shell"
      command = "{shell}"
"""
REFRESH = 10
RUNTIME = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR") or pathlib.Path.home() / ".cache")
WMCTRL = shutil.which("wmctrl")


def get_config(command):
    agent, shell = shlex.join(command), shlex.join([*command, "--shell"])
    if '"' in shell:
        sys.exit(f"a double quote cannot go in a terminator layout: {shell}")
    try:
        lines = (CONFIG_ROOT / "terminator" / "config").read_text().splitlines(keepends=True)
    except OSError:
        lines = []
    if LAYOUTS not in [line.strip() for line in lines]:
        lines.append(f"\n{LAYOUTS}\n")
    at = [line.strip() for line in lines].index(LAYOUTS) + 1
    lines.insert(at, PANES.format(layout=LAYOUT, agent=agent, shell=shell))
    return "".join(lines)


def get_title(bundle, *statuses):
    command = [sys.executable, str(HERE / "client" / "window-title.py"), bundle, *statuses]
    title = subprocess.run(command, capture_output=True, check=False, text=True).stdout.strip()
    return title or bundle


def get_record(bundle, since):
    """The record of the newest claude of the window: client/agent-run.sh names it after the
    bundle, and the claude of an ops window has the session of its name."""
    session = bundle.removeprefix(OPS) if bundle.startswith(OPS) else None
    folder = HOST_SESSIONS if session else CLAUDE_CONFIG / bundle / "sessions"
    key, value = ("sessionId", session) if session else ("name", bundle)
    records = []
    for path in folder.glob("*.json"):
        try:
            data = path.read_bytes()
            record = json.loads(data) if value.encode() in data else {}
        except (OSError, ValueError):
            continue
        if record.get(key) == value and record.get("startedAt", 0) >= since:
            records.append(record)
    return max(records, key=lambda record: record["startedAt"], default=None)


def is_running(bundle):
    """Whether the agent of the window runs: agent-terminal.py BUNDLE, its shell aside, and this
    process aside, which holds it as COMMAND."""
    for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        if path.parent.name == str(os.getpid()):
            continue
        try:
            args = path.read_bytes().split(b"\0")
        except OSError:
            continue
        if bundle.encode() in args and b"--shell" not in args:
            if any(arg.endswith(b"agent-terminal.py") for arg in args):
                return True
    return False


def get_windows(pid):
    out = subprocess.run([WMCTRL, "-l", "-p"], capture_output=True, check=False, text=True)
    lines = [line.split() for line in out.stdout.splitlines()]
    return [line[0] for line in lines if line[2:3] == [str(pid)]]


def minimize(window):
    """Iconify a window as `xdotool windowminimize` does, which wmctrl cannot."""
    x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
    x11.XIconifyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(None)
    if display:
        x11.XIconifyWindow(display, int(window, 16), x11.XDefaultScreen(display))
        x11.XCloseDisplay(display)


def follow(bundle, terminator, since, hide=True):
    signal.signal(signal.SIGUSR1, lambda *args: terminator.terminate())
    done = get_agent_folder(bundle) / "done"
    ended = False
    refreshed = time.monotonic()
    shown = titled = None
    while True:
        try:
            return terminator.wait(HIDE_CHECK if hide else CHECK)
        except subprocess.TimeoutExpired:
            pass
        if hide and (windows := get_windows(terminator.pid)):
            for window in windows:
                minimize(window)
            hide = False
        record = get_record(bundle, since)
        if bundle.startswith(OPS) and time.monotonic() - refreshed >= REFRESH:
            refreshed, shown = time.monotonic(), None
            ended = ended or (bool(record) and not is_running(bundle))
        status = record and f"{record['sessionId']}={record.get('status') or ''}"
        if not status and not shown and (get_mtime(done) or 0) * 1000 >= since:
            status = "--failed"
        if not status or status == shown:
            continue
        title = get_title(bundle) if ended else get_title(bundle, status)
        windows = get_windows(terminator.pid)
        for window in windows if title != titled else []:
            subprocess.run([WMCTRL, "-u", "-i", "-r", window, "-N", title], check=False)
        if windows:
            shown, titled = status, title


def main():
    bundle, *command = sys.argv[1:]
    path = RUNTIME / "bundle-ctl" / f"terminator-{bundle}.config"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(get_config(command))
    terminator = ["terminator", "--no-dbus", "--config", str(path), "--layout", LAYOUT]
    os.environ["BUNDLE_CTL_AGENT_WINDOW"] = bundle
    os.environ["BUNDLE_CTL_WINDOW_PID"] = str(os.getpid())
    if not WMCTRL:
        os.execvp(terminator[0], [*terminator, "--title", get_title(bundle)])
    terminator += ["--title", get_title(bundle, "--starting")]
    agent = get_agent_folder(bundle)
    hide = not bundle.startswith(OPS) or (agent / "task.md").is_file()
    since = time.time() * 1000
    code = follow(bundle, subprocess.Popen(terminator), since, hide)
    if bundle.startswith(OPS):
        shutil.rmtree(agent, ignore_errors=True)
    sys.exit(code)


if __name__ == "__main__":
    main()
