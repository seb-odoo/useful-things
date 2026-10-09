#!/usr/bin/env python3
"""Open a terminator window split in two: the agent of a bundle, and a host shell in its folder.

    agent-terminator.py BUNDLE COMMAND...

For AGENT_TERMINAL of the bundle-ctl daemon, which appends COMMAND, the one that runs the agent
(agent-terminal.py BUNDLE). The same command with --shell runs the shell.

Terminator takes the commands of a split from a layout of its config only. So the window runs on a
copy of the user's config with that layout added: preferences saved from it go to the copy.

The title of each pane says where it runs. A pane split by hand has no title: the host .bashrc
prints the same in any shell that holds BUNDLE_CTL_AGENT_WINDOW, the bundle name.
"""

import os
import pathlib
import shlex
import sys

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


def main():
    bundle, *command = sys.argv[1:]
    path = RUNTIME / "bundle-ctl" / f"terminator-{bundle}.config"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(get_config(command))
    os.environ["BUNDLE_CTL_AGENT_WINDOW"] = bundle
    os.execvp(
        "terminator",
        ["terminator", "--no-dbus", "--config", str(path), "--layout", LAYOUT, "--title", bundle],
    )


if __name__ == "__main__":
    main()
