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
stays.
"""

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from agents import get_agent_folder, get_mtime  # noqa: E402
from config import CLAUDE_CONFIG_CONTAINER  # noqa: E402

CHECK = 1
CLAUDE_CONFIG = pathlib.Path(CLAUDE_CONFIG_CONTAINER)
CONFIG_ROOT = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
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
    bundle."""
    records = []
    for path in (CLAUDE_CONFIG / bundle / "sessions").glob("*.json"):
        try:
            data = path.read_bytes()
            record = json.loads(data) if bundle.encode() in data else {}
        except (OSError, ValueError):
            continue
        if record.get("name") == bundle and record.get("startedAt", 0) >= since:
            records.append(record)
    return max(records, key=lambda record: record["startedAt"], default=None)


def follow(bundle, terminator, since):
    done = get_agent_folder(bundle) / "done"
    shown = None
    while True:
        try:
            return terminator.wait(CHECK)
        except subprocess.TimeoutExpired:
            pass
        record = get_record(bundle, since)
        status = record and f"{record['sessionId']}={record.get('status') or ''}"
        if not status and not shown and (get_mtime(done) or 0) * 1000 >= since:
            status = "--failed"
        if not status or status == shown:
            continue
        title = get_title(bundle, status)
        out = subprocess.run([WMCTRL, "-l", "-p"], capture_output=True, check=False, text=True)
        lines = [line.split() for line in out.stdout.splitlines()]
        windows = [line[0] for line in lines if line[2:3] == [str(terminator.pid)]]
        for window in windows:
            subprocess.run([WMCTRL, "-u", "-i", "-r", window, "-N", title], check=False)
        if windows:
            shown = status


def main():
    bundle, *command = sys.argv[1:]
    path = RUNTIME / "bundle-ctl" / f"terminator-{bundle}.config"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(get_config(command))
    terminator = ["terminator", "--no-dbus", "--config", str(path), "--layout", LAYOUT]
    os.environ["BUNDLE_CTL_AGENT_WINDOW"] = bundle
    if not WMCTRL:
        os.execvp(terminator[0], [*terminator, "--title", get_title(bundle)])
    terminator += ["--title", get_title(bundle, "--starting")]
    since = time.time() * 1000
    sys.exit(follow(bundle, subprocess.Popen(terminator), since))


if __name__ == "__main__":
    main()
