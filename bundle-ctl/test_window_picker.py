#!/usr/bin/env python3
"""How window-picker.py opens rofi, the rows it gives it, its counts, the window it brings up on a
pick, with a fake rofi, wmctrl and xdg-open, and the windows of a file of its own shown, the
others minimized: python3 test_window_picker.py"""

import importlib.util
import json
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
0x0d 0 terminator.Terminator box ⏩ master-f-fw--seb (fw)
"""
OPS = "✋ 🔧 Ops list layout (ops)\0info\x1f0x0a\x1fmeta\x1fwaiting"
COUNTS = "❌ failed 0   ✅ finished 1   ✋ waiting 2   ⏳ running 1   🚀 starting 0"
REVIEW = "✋ 📝 master-c-aku (review)\0info\x1fmaster-c-aku\x1fmeta\x1fwaiting"
ROWS = [
    f"\0message\x1f{COUNTS}   |   click: VS Code   right click: terminal",
    "\0no-custom\x1ftrue",
    "\0use-hot-keys\x1ftrue",
    "➕ new ops agent\0info\x1fnew-ops",
    OPS,
    "✅ 🤖 master-d-runbot-12--seb (runbot)\0info\x1fmaster-d-runbot-12--seb\x1fmeta\x1ffinished",
    REVIEW,
    "⏳ ⭐ master-b--seb (mine)   Waiting for you: hok g12\0info\x1fmaster-b--seb\x1fmeta\x1frunning",
    "🔧 Refactor (ops)\0info\x1f0x0b\x1fmeta\x1f",
    "⏩ master-f-fw--seb (fw)\0info\x1fmaster-f-fw--seb\x1fmeta\x1f",
    "⭐ master-e--seb (mine)\0info\x1fmaster-e--seb\x1fmeta\x1f",
    "Inbox - Mozilla Firefox\0info\x1f0x03\x1fmeta\x1f",
    "🔧 Tools (ops) - Mozilla Firefox\0info\x1f0x0c\x1fmeta\x1f",
]
AGENTS = (
    "master-b--seb",
    "master-c-aku",
    "master-d-runbot-12--seb",
    "master-f-fw--seb",
    "master-g-runbot-13--seb",
)
RESTORED = [
    ROWS[0].replace("✋ waiting 2", "✋ waiting 0"),
    *[row for row in ROWS[1:] if row not in (OPS, REVIEW)],
]
WAITING = f"""{WINDOWS.replace("⏩", "✋ ⏩")}\
0x0e 0 terminator.Terminator box ✋ 🤖 master-g-runbot-13--seb (runbot)
0x0f 0 terminator.Terminator box ❌ 📝 master-h-aku (review)
"""
WAITING_ROWS = [
    "❌ 📝 master-h-aku (review)\0info\x1fmaster-h-aku\x1fmeta\x1ffailed",
    OPS,
    "✋ ⏩ master-f-fw--seb (fw)\0info\x1fmaster-f-fw--seb\x1fmeta\x1fwaiting",
    ROWS[5],
    "✋ 🤖 master-g-runbot-13--seb (runbot)\0info\x1fmaster-g-runbot-13--seb\x1fmeta\x1fwaiting",
    REVIEW,
]
OPENING = [f"🔄 {row}" if row == REVIEW else row for row in ROWS]
DEAD = [
    ROWS[0].replace("❌ failed 0", "❌ failed 1"),
    *ROWS[1:4],
    "❌ ⭐ master-e--seb (mine)\0info\x1fmaster-e--seb\x1fmeta\x1ffailed",
    *[row for row in ROWS[4:] if "master-e--seb" not in row],
]
WAIT = 'printf "\\033]0;%s\\007\\n%s\\nPress Enter to close.\\n" "$0" "$0"; read -r _'
ENDED = [
    ROWS[0].replace("✋ waiting 2", "✋ waiting 1"),
    *ROWS[1:6],
    *ROWS[7:11],
    "🔄 📝 master-c-aku (review)\0info\x1fmaster-c-aku\x1fmeta\x1f",
    *ROWS[11:],
]
RUN = """\
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
import test_window_picker
picker = test_window_picker.load_picker()
folder = pathlib.Path(sys.argv[2])
test_window_picker.hide(picker, folder)
picker.get_agent_folder = lambda name: folder / name / ".agent"
sys.argv[1:] = sys.argv[3:]
picker.main()
"""
SHOWN_ROWS = [
    ("0x0a", False),
    ("master-d-runbot-12--seb", True),
    ("master-c-aku", False),
    ("master-b--seb", True),
    ("0x0b", True),
    ("master-f-fw--seb", True),
    ("master-e--seb", True),
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
ANSWERS = [
    ("S-b", "pushed: c4d3b85\n\n**Waiting for you**: hok g12", "master-b--seb"),
    ("S-e", "pushed: c4d3b85", "master-e--seb"),
    ("S-ops", "**Done**\n- 3/3\n\nNothing left: pushed.\n\nSeparately: the tour is queued.", None),
]
CLOSE_LINES = [
    {
        "master-b--seb": "Waiting for you: hok g12",
        "master-e--seb": "",
        "master-z--seb": "",
        "0x0a": "Nothing left: pushed.",
        "0x0b": "",
    },
    "Nothing left: merged.",
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
    env["CLAUDE_CONFIG_DIR"] = str(folder)
    command = [sys.executable, "-c", RUN, str(HERE / "window-picker.py"), str(folder), *args]
    return subprocess.run(command, capture_output=True, check=True, env=env, text=True).stdout


def hide(picker, folder):
    """Give picker the windows of the file `shown` of folder as shown, the others as minimized."""
    shown = (folder / "shown").read_text().split()
    picker.get_hidden = lambda windows: {window for window in windows if window not in shown}


def start_agent(name):
    code = "import time; time.sleep(60)"
    return subprocess.Popen([sys.executable, "-c", code, str(HERE / "agent-terminal.py"), name])


def start_window(name):
    """The agent-terminator.py of a window, which holds agent-terminal.py NAME as its COMMAND."""
    code = "import time; time.sleep(60)"
    terminal = [sys.executable, str(HERE / "agent-terminal.py"), name]
    command = [sys.executable, "-c", code, str(HERE / "agent-terminator.py"), name, *terminal]
    return subprocess.Popen(command)


def load_picker():
    spec = importlib.util.spec_from_file_location("picker", HERE / "window-picker.py")
    picker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(picker)
    return picker


def get_shown_rows(folder):
    """The rows agent-list.py asks for, the windows not minimized included: (key, minimized)."""
    env = dict(os.environ)
    os.environ.update(PATH=f"{folder}:{env['PATH']}", XDG_RUNTIME_DIR=str(folder))
    try:
        picker = load_picker()
        hide(picker, folder)
        rows = picker.get_rows(picker.get_windows(), shown=True)
    finally:
        os.environ.clear()
        os.environ.update(env)
    return [(row.key, row.minimized) for row in rows]


def get_new_ops():
    """The command a pick of the first row starts, in this process: a real bctl reaches the
    daemon."""
    picker = load_picker()
    started = []
    picker.subprocess.Popen = lambda command, **kwargs: started.append(command[1:])
    os.environ.update(ROFI_RETV="1", ROFI_INFO="new-ops")
    picker.main()
    return started


def write_answer(folder, session, answer, bundle=None):
    """A transcript whose last answer is answer, and the .agent/session of bundle that names it."""
    if bundle:
        (folder / bundle / ".agent").mkdir(parents=True, exist_ok=True)
        (folder / bundle / ".agent" / "session").write_text(f"{session}\n")
    path = folder / "projects" / "-workspace" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}}
    path.write_text(f"{json.dumps(entry)}\n")


def get_close_lines(folder):
    """The close line of the session of each row: the session of the .agent/ folder of a bundle,
    the one an ops window is named after; none with no session or no close line. Read again once
    the transcript changed."""
    picker = load_picker()
    picker.get_agent_folder = lambda name: folder / name / ".agent"
    picker.get_terminator = lambda pid: (5, "ops-S-ops") if pid == "77" else None
    picker.load_title().PROJECTS = folder / "projects"
    picker.read_window_pids = lambda: {"0x0a": "77", "0x0b": "78"}
    rows = [
        picker.Row(None, 0, "", key, window, True)
        for key, window in (
            ("master-b--seb", "0x02"),
            ("master-e--seb", "0x09"),
            ("master-z--seb", "0x0e"),
            ("0x0a", "0x0a"),
            ("0x0b", "0x0b"),
        )
    ]
    found = [picker.get_close_lines(rows)]
    write_answer(folder, "S-b", "pushed: c4d3b85\n\nNothing left: merged.")
    found.append(picker.get_close_lines(rows)["master-b--seb"])
    return found


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
        for session, answer, bundle in ANSWERS:
            write_answer(folder, session, answer, bundle)
        (folder / "pids").write_text("")
        listed = f'{{ [ "$2" = -p ] && cat {folder}/pids || cat {folder}/windows; }}'
        write_fake(folder, "wmctrl", f'[ "$1" = -l ] && {listed} || echo wmctrl "$@" >> {log}')
        write_fake(folder, "xdg-open", f'echo xdg-open "$@" >> {log}; echo code --folder-uri x')
        write_fake(folder, "rofi", f'for arg in "$@"; do echo "[$arg]"; done > {folder}/rofi.log')
        (folder / "shown").write_text("")
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
        (folder / "pids").write_text(f"0x09 0 {os.getpid()} box ⭐ master-e--seb (mine)\n")
        ending = "master-e--seb: claude ended with code 137"
        dead = subprocess.Popen(["sh", "-c", WAIT, ending], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        try:
            rows = run(folder).splitlines()
            counts = run(folder, "--counts").splitlines()
        finally:
            dead.kill()
            dead.wait()
        if rows != DEAD:
            failures.append(f"rows once a claude ended on an error {rows!r}, expected {DEAD!r}")
        if counts[:1] != ["❌ 1"]:
            failures.append(f"counts once a claude ended on an error {counts!r}, expected ❌ 1")
        (folder / "windows").write_text(WINDOWS.replace("⭐ master-e", "🚀 ⭐ master-e"))
        counts = run(folder, "--counts").splitlines()
        if counts[:1] != ["✋ 2"]:
            failures.append(f"counts while a bundle starts {counts!r}, expected ✋ 2 before 🚀")
        (folder / "windows").write_text(WAITING)
        rows = run(folder).splitlines()[4:10]
        if rows != WAITING_ROWS:
            failures.append(f"rows while fw and runbot wait {rows!r}, expected {WAITING_ROWS!r}")
        counts = run(folder, "--counts").splitlines()
        if counts[:1] != ["❌ 1"]:
            failures.append(f"counts while an agent failed {counts!r}, expected ❌ 1 before ✋")
        (folder / "windows").write_text(WINDOWS)
        (folder / "shown").write_text("0x04\n0x0a\n")
        rows = run(folder).splitlines()
        if rows != RESTORED:
            failures.append(f"rows while 2 terminals are restored {rows!r}, expected {RESTORED!r}")
        counts = run(folder, "--counts").splitlines()
        if counts[:1] != ["✅ 1"]:
            failures.append(f"counts while 2 terminals are restored {counts!r}, expected ✅ 1")
        if (found := get_shown_rows(folder)) != SHOWN_ROWS:
            failures.append(f"rows of agent-list.py {found!r}, expected {SHOWN_ROWS!r}")
        (folder / "shown").write_text("")
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
        starting = WINDOWS.replace("✋ 🔧", "🔧").replace("⭐ master-e", "🚀 ⭐ master-e")
        (folder / "windows").write_text(starting)
        counts = run(folder, "--counts").splitlines()
        if counts[:1] != ["✅ 1"]:
            failures.append(f"counts once nothing waits {counts!r}, expected ✅ 1 before 🚀")
        (folder / "windows").write_text(starting.replace("✅ ", ""))
        counts = run(folder, "--counts").splitlines()
        if counts[:1] != ["🚀 1"]:
            failures.append(f"counts once nothing waits {counts!r}, expected 🚀 1 before ⏳")
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
        for found, expected in zip(get_close_lines(folder), CLOSE_LINES, strict=True):
            if found != expected:
                failures.append(f"close lines {found!r}, expected {expected!r}")
    new_ops = [str(HERE / "client" / "bctl.py"), "open", "ops", "--terminal"]
    if (started := get_new_ops()) != [new_ops]:
        failures.append(f"pick 1 new-ops: started {started!r}, expected {[new_ops]!r}")
    for line in failures:
        print(line)
    cases = 20 + len(PICKS) + len(CLOSE_LINES)
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
