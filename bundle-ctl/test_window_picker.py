#!/usr/bin/env python3
"""The rows window-picker.py gives rofi, its counts, the window it brings up on a pick, with a fake
wmctrl: python3 test_window_picker.py"""

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
0x06 0 terminator.Terminator box ✋ ⭐ master-a--seb (mine)
0x07 0 N/A box
0x08 0 code.Code box ✋ ⭐ master-a--seb (mine) - models.js
"""
COUNTS = "✅ finished 1   ✋ waiting 2   ❌ failed 0   ⏳ running 1   🚀 starting 0"
ROWS = [
    f"\0message\x1f{COUNTS}",
    "\0no-custom\x1ftrue",
    "✅ 🤖 master-d-runbot-12--seb (runbot)\0info\x1f0x05\x1fmeta\x1ffinished",
    "✋ ⭐ master-a--seb (mine)\0info\x1f0x06\x1fmeta\x1fwaiting",
    "✋ ⭐ master-a--seb (mine) - models.js\0info\x1f0x08\x1fmeta\x1fwaiting",
    "✋ 📝 master-c-aku (review)\0info\x1f0x04\x1fmeta\x1fwaiting",
    "⏳ ⭐ master-b--seb (mine)\0info\x1f0x02\x1fmeta\x1frunning",
    "Inbox - Mozilla Firefox\0info\x1f0x03\x1fmeta\x1f",
]


def pick(folder, *args, **env):
    env = {**os.environ, "PATH": f"{folder}:{os.environ['PATH']}", **env}
    command = [sys.executable, str(HERE / "window-picker.py"), *args]
    return subprocess.run(command, capture_output=True, check=True, env=env, text=True).stdout


def main():
    failures = []
    with tempfile.TemporaryDirectory() as root:
        folder = pathlib.Path(root)
        (folder / "windows").write_text(WINDOWS)
        wmctrl = folder / "wmctrl"
        wmctrl.write_text(
            "#!/bin/sh\n"
            f'[ "$1" = -l ] && cat {folder}/windows || echo "$@" >> {folder}/log\n'
        )
        wmctrl.chmod(0o755)
        rows = pick(folder).splitlines()
        if rows != ROWS:
            failures.append(f"rows {rows!r}, expected {ROWS!r}")
        counts = pick(folder, "--counts").splitlines()
        if counts != ["✋ 2", COUNTS]:
            failures.append(f"counts {counts!r}, expected {['✋ 2', COUNTS]!r}")
        found = pick(folder, ROFI_RETV="1", ROFI_INFO="0x04")
        log = (folder / "log").read_text().splitlines()
        if found or log != ["-i -a 0x04"]:
            failures.append(f"a pick printed {found!r} and ran wmctrl {log!r}, expected -i -a 0x04")
    for line in failures:
        print(line)
    cases = 3
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
