#!/usr/bin/env python3
"""How window-picker.py opens rofi, the rows it gives it, its counts, the window it brings up on a
pick, with a fake rofi, wmctrl and xdg-open: python3 test_window_picker.py"""

import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
WINDOWS = """\
0x01 -1 nemo-desktop.Nemo-desktop box Desktop
0x02 0 terminator.Terminator box ⏳ ⭐ master-b--seb (mine)
0x03 0 firefox.Firefox box Inbox - Mozilla Firefox
0x04 1 terminator.Terminator box ✋ 📝 master-c-aku (review)
0x05 0 terminator.Terminator box ✅ 🤖 master-d-runbot-12--seb (runbot)
0x06 0 terminator.Terminator box ✅ ⭐ master-a--seb (mine)
0x07 0 N/A box
0x08 0 code.Code box ✋ ⭐ master-a--seb (mine) - models.js
0x09 0 terminator.Terminator box ⭐ master-e--seb (mine)
"""
COUNTS = "✅ finished 1   ✋ waiting 2   ❌ failed 0   ⏳ running 1   🚀 starting 0"
ROWS = [
    f"\0message\x1f{COUNTS}   |   click: VS Code   right click: terminal",
    "\0no-custom\x1ftrue",
    "\0use-hot-keys\x1ftrue",
    "✅ 🤖 master-d-runbot-12--seb (runbot)\0info\x1fmaster-d-runbot-12--seb\x1fmeta\x1ffinished",
    "✋ ⭐ master-a--seb (mine)\0info\x1fmaster-a--seb\x1fmeta\x1fwaiting",
    "✋ 📝 master-c-aku (review)\0info\x1fmaster-c-aku\x1fmeta\x1fwaiting",
    "⏳ ⭐ master-b--seb (mine)\0info\x1fmaster-b--seb\x1fmeta\x1frunning",
    "Inbox - Mozilla Firefox\0info\x1f0x03\x1fmeta\x1f",
    "⭐ master-e--seb (mine)\0info\x1fmaster-e--seb\x1fmeta\x1f",
]
SHOWN = [
    "[-modi]",
    f"[windows:{HERE / 'window-picker.py'}]",
    "[-show]",
    "[windows]",
    "[-hover-select]",
    "[-me-select-entry]",
    "[]",
    "[-me-accept-entry]",
    "[MousePrimary]",
    "[-kb-accept-alt]",
    "[]",
    "[-kb-custom-1]",
    "[Shift+Return,MouseSecondary]",
]
PICKS = [
    ("1", "0x03", "wmctrl -i -a 0x03"),
    ("1", "master-a--seb", "wmctrl -i -a 0x08"),
    ("10", "master-a--seb", "wmctrl -i -a 0x06"),
    ("1", "master-c-aku", "xdg-open odoo-bundle://master-c-aku"),
    ("10", "master-c-aku", "wmctrl -i -a 0x04"),
]


def run(folder, *args, **env):
    env = {**os.environ, "PATH": f"{folder}:{os.environ['PATH']}", **env}
    command = [sys.executable, str(HERE / "window-picker.py"), *args]
    return subprocess.run(command, capture_output=True, check=True, env=env, text=True).stdout


def write_fake(folder, name, script):
    path = folder / name
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)


def main():
    failures = []
    with tempfile.TemporaryDirectory() as root:
        folder = pathlib.Path(root)
        log = folder / "log"
        (folder / "windows").write_text(WINDOWS)
        write_fake(folder, "wmctrl", f'[ "$1" = -l ] && cat {folder}/windows || echo wmctrl "$@" >> {log}')
        write_fake(folder, "xdg-open", f'echo xdg-open "$@" >> {log}')
        write_fake(folder, "rofi", f'for arg in "$@"; do echo "[$arg]"; done > {folder}/rofi.log')
        run(folder, "--show")
        found = (folder / "rofi.log").read_text().split()
        if found != SHOWN:
            failures.append(f"--show ran rofi {found!r}, expected {SHOWN!r}")
        rows = run(folder).splitlines()
        if rows != ROWS:
            failures.append(f"rows {rows!r}, expected {ROWS!r}")
        counts = run(folder, "--counts").splitlines()
        if counts != ["✋ 2", COUNTS]:
            failures.append(f"counts {counts!r}, expected {['✋ 2', COUNTS]!r}")
        for retv, info, expected in PICKS:
            log.write_text("")
            found = run(folder, ROFI_RETV=retv, ROFI_INFO=info)
            for _ in range(50):
                if log.read_text():
                    break
                subprocess.run(["sleep", "0.02"], check=True)
            ran = log.read_text().splitlines()
            if found or ran != [expected]:
                failures.append(f"pick {retv} {info}: printed {found!r}, ran {ran!r}")
    for line in failures:
        print(line)
    cases = 3 + len(PICKS)
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
