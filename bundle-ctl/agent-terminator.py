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
(window-picker.py). Terminator maps it minimized with --hidden, only when its hide_window key is
not bound: the copy of the config unbinds it.

An ops agent window (ops-SESSION) follows the record of that session in the sessions folder of
the ops container, and the title of its conversation, which changes with no change of status. Its mark goes once its claude ended, as the window list cannot tell which ops
window has an agent left. Opened on a new session with no task, it waits for Seb's first prompt:
it shows in front. When a tab of the ops VS Code window resumes its session, the window closes, as
two claude processes on one session would each go on from where they loaded it.
"""

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

from agents import OPS, get_agent_folder, get_mtime, get_transcript, is_agent_running  # noqa: E402
from config import CLAUDE_CONFIG_CONTAINER, HOST_FOLDER  # noqa: E402

CHECK = 1
CLAUDE_CONFIG = pathlib.Path(CLAUDE_CONFIG_CONTAINER)
CONFIG_ROOT = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
HIDE_KEY = "hide_window"
KEYBINDINGS = "[keybindings]"
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
    lines = [line for line in lines if line.split("=")[0].strip() != HIDE_KEY]
    insert(lines, KEYBINDINGS, f"  {HIDE_KEY} = None\n")
    insert(lines, LAYOUTS, PANES.format(layout=LAYOUT, agent=agent, shell=shell))
    return "".join(lines)


def insert(lines, section, text):
    if section not in [line.strip() for line in lines]:
        lines.append(f"\n{section}\n")
    lines.insert([line.strip() for line in lines].index(section) + 1, text)


def get_title(bundle, *statuses):
    command = [sys.executable, str(HERE / "client" / "window-title.py"), bundle, *statuses]
    title = subprocess.run(command, capture_output=True, check=False, text=True).stdout.strip()
    return title or bundle


def get_record(bundle, since):
    """The record of the newest claude of the window, in the sessions folder of its container:
    client/agent-run.sh names it after the bundle, and the claude of an ops window has the session
    of its name."""
    session = bundle.removeprefix(OPS) if bundle.startswith(OPS) else None
    container = os.path.basename(HOST_FOLDER or "") if session else bundle
    key, value = ("sessionId", session) if session else ("name", bundle)
    records = []
    for path in (CLAUDE_CONFIG / container / "sessions").glob("*.json"):
        try:
            data = path.read_bytes()
            record = json.loads(data) if value.encode() in data else {}
        except (OSError, ValueError):
            continue
        if record.get(key) == value and record.get("startedAt", 0) >= since:
            records.append(record)
    return max(records, key=lambda record: record["startedAt"], default=None)


def get_windows(pid):
    out = subprocess.run([WMCTRL, "-l", "-p"], capture_output=True, check=False, text=True)
    lines = [line.split() for line in out.stdout.splitlines()]
    return [line[0] for line in lines if line[2:3] == [str(pid)]]


def follow(bundle, terminator, since):
    signal.signal(signal.SIGUSR1, lambda *args: terminator.terminate())
    done = get_agent_folder(bundle) / "done"
    ended = False
    refreshed = time.monotonic()
    shown = titled = None
    while True:
        try:
            return terminator.wait(CHECK)
        except subprocess.TimeoutExpired:
            pass
        record = get_record(bundle, since)
        if record and record.get("entrypoint") == "claude-vscode":
            terminator.terminate()
            continue
        if bundle.startswith(OPS) and time.monotonic() - refreshed >= REFRESH:
            refreshed, shown = time.monotonic(), None
            ended = ended or (bool(record) and not is_agent_running(bundle))
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
    fresh = bundle.startswith(OPS) and not (agent / "task.md").is_file()
    fresh = fresh and not get_transcript(bundle.removeprefix(OPS))
    terminator += [] if fresh else ["--hidden"]
    since = time.time() * 1000
    code = follow(bundle, subprocess.Popen(terminator), since)
    if bundle.startswith(OPS):
        shutil.rmtree(agent, ignore_errors=True)
    sys.exit(code)


if __name__ == "__main__":
    main()
