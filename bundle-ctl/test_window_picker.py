#!/usr/bin/env python3
"""How window-picker.py opens rofi, the rows it gives it, its counts, the window it brings up on a
pick, with a fake rofi, wmctrl and xdg-open: python3 test_window_picker.py"""

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import time

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
0x0a 0 terminator.Terminator box ✋ 🔧 Ops list layout (ops)
0x0b 0 terminator.Terminator box 🔧 Refactor (ops)
0x0c 0 firefox.Firefox box 🔧 Tools (ops) - Mozilla Firefox
"""
OPS = "✋ 🔧 Ops list layout (ops)\0info\x1f0x0a\x1fmeta\x1fwaiting"
COUNTS = "✅ finished 1   ✋ waiting 2   ❌ failed 0   ⏳ running 1   🚀 starting 0"
REVIEW = "✋ 📝 master-c-aku (review)\0info\x1fmaster-c-aku\x1fmeta\x1fwaiting"
ROWS = [
    f"\0message\x1f{COUNTS}   |   click: VS Code   right click: terminal",
    "\0no-custom\x1ftrue",
    "\0use-hot-keys\x1ftrue",
    "➕ new ops agent\0info\x1fnew-ops",
    "✅ 🤖 master-d-runbot-12--seb (runbot)\0info\x1fmaster-d-runbot-12--seb\x1fmeta\x1ffinished",
    REVIEW,
    OPS,
    "⏳ ⭐ master-b--seb (mine)\0info\x1fmaster-b--seb\x1fmeta\x1frunning",
    "Inbox - Mozilla Firefox\0info\x1f0x03\x1fmeta\x1f",
    "⭐ master-e--seb (mine)\0info\x1fmaster-e--seb\x1fmeta\x1f",
    "🔧 Refactor (ops)\0info\x1f0x0b\x1fmeta\x1f",
    "🔧 Tools (ops) - Mozilla Firefox\0info\x1f0x0c\x1fmeta\x1f",
]
AGENTS = ("master-b--seb", "master-c-aku", "master-d-runbot-12--seb")
OPENING = [f"🔄 {REVIEW}" if row == OPS else OPS if row == REVIEW else row for row in ROWS]
ENDED = [
    ROWS[0].replace("✋ waiting 2", "✋ waiting 1"),
    *ROWS[1:5],
    *ROWS[6:10],
    "🔄 📝 master-c-aku (review)\0info\x1fmaster-c-aku\x1fmeta\x1f",
    *ROWS[10:],
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
    env = {**os.environ, "PATH": f"{folder}:{os.environ['PATH']}", "XDG_RUNTIME_DIR": str(folder), **env}
    command = [sys.executable, str(HERE / "window-picker.py"), *args]
    return subprocess.run(command, capture_output=True, check=True, env=env, text=True).stdout


def start_agent(name):
    code = "import time; time.sleep(60)"
    return subprocess.Popen([sys.executable, "-c", code, str(HERE / "agent-terminal.py"), name])


def start_window(name):
    """The agent-terminator.py of a window, which holds agent-terminal.py NAME as its COMMAND."""
    code = "import time; time.sleep(60)"
    terminal = [sys.executable, str(HERE / "agent-terminal.py"), name]
    command = [sys.executable, "-c", code, str(HERE / "agent-terminator.py"), name, *terminal]
    return subprocess.Popen(command)


def get_new_ops():
    """The command a pick of the first row starts, in this process: a real bctl reaches the
    daemon."""
    spec = importlib.util.spec_from_file_location("picker", HERE / "window-picker.py")
    picker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(picker)
    started = []
    picker.subprocess.Popen = lambda command, **kwargs: started.append(command[1:])
    os.environ.update(ROFI_RETV="1", ROFI_INFO="new-ops")
    picker.main()
    return started


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
        write_fake(folder, "xdg-open", f'echo xdg-open "$@" >> {log}; echo code --folder-uri x')
        write_fake(folder, "rofi", f'for arg in "$@"; do echo "[$arg]"; done > {folder}/rofi.log')
        agents = {name: start_agent(name) for name in AGENTS}
        windows = [start_window(name) for name in AGENTS]
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
        old = folder / "bundle-ctl" / "opening" / "master-b--seb"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.touch()
        os.utime(old, (time.time() - 600,) * 2)
        opened = old.with_name("master-a--seb")
        opened.touch()
        rows = run(folder).splitlines()
        if rows != OPENING:
            failures.append(f"rows while VS Code opens {rows!r}, expected {OPENING!r}")
        if opened.exists():
            failures.append("the mark of a bundle open in VS Code stays")
        agents.pop("master-c-aku").kill()
        rows = run(folder).splitlines()
        if rows != ENDED:
            failures.append(f"rows once a claude ended {rows!r}, expected {ENDED!r}")
        counts = run(folder, "--counts").splitlines()
        if counts != ["✋ 1", COUNTS.replace("✋ waiting 2", "✋ waiting 1")]:
            failures.append(f"counts once no bundle waits {counts!r}, expected ✋ 1 for ops")
        (folder / "windows").write_text(WINDOWS.replace("✋ 🔧", "🔧"))
        counts = run(folder, "--counts").splitlines()
        if counts[:1] != ["✅ 1"]:
            failures.append(f"counts once nothing waits {counts!r}, expected ✅ 1 before ⏳")
        (folder / "windows").write_text(WINDOWS.replace("✋ 🔧", "🔧").replace("✅ ", ""))
        counts = run(folder, "--counts").splitlines()
        if counts[:1] != ["⏳ 1"]:
            failures.append(f"counts once nothing waits {counts!r}, expected ⏳ 1")
        for agent in [*agents.values(), *windows]:
            agent.kill()
            agent.wait()
        counts = run(folder, "--counts").splitlines()
        if counts[:1] != ["🪟"]:
            failures.append(f"counts once no claude runs {counts!r}, expected 🪟")
    new_ops = [str(HERE / "client" / "bctl.py"), "open", "ops", "--terminal"]
    if (started := get_new_ops()) != [new_ops]:
        failures.append(f"pick 1 new-ops: started {started!r}, expected {[new_ops]!r}")
    for line in failures:
        print(line)
    cases = 11 + len(PICKS)
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
