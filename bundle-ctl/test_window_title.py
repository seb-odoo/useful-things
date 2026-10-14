#!/usr/bin/env python3
"""The mark of what Claude does in the title of a bundle window, and agent-terminator.py following
it with a fake wmctrl: python3 test_window_title.py"""

import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
BUNDLE = "master-x--seb"
TITLE = f"⭐ {BUNDLE} (mine)"

ANSWERS = {
    "bold": ["**Nothing to do:** merged since"],
    "chat": ["pushed: 1111111", "The tests pass."],
    "decide": ["The list shows it.\n\n**Done**\n- 18/18\n\n**Decide**\n- the icon"],
    "pushed": ["pushed: c4d3b85\n\nDone"],
    "ready": ["ready to push"],
    "report": ["Which icon?", "The list shows it.\n\n## Done\n- 18/18 cases as expected"],
    "seb": ["needs Seb: which base?"],
}
MARKS = [
    ([], TITLE),
    (["--failed"], f"❌ {TITLE}"),
    (["--starting"], f"🚀 {TITLE}"),
    (["a=busy"], f"⏳ {TITLE}"),
    (["a=shell"], f"⏳ {TITLE}"),
    (["a=waiting", "b=busy"], f"✋ {TITLE}"),
    (["pushed=idle"], f"✅ {TITLE}"),
    (["bold=idle"], f"✅ {TITLE}"),
    (["chat=idle"], f"✋ {TITLE}"),
    (["seb=idle"], f"✋ {TITLE}"),
    (["ready=idle"], f"✋ {TITLE}"),
    (["pushed=idle", "seb=idle"], f"✋ {TITLE}"),
    (["missing="], f"✋ {TITLE}"),
]
OPS = "🔧 Ops windows in the list (ops)"
OPS_MARKS = [
    ("ops-report", [], OPS),
    ("ops-report", ["--starting"], f"🚀 {OPS}"),
    ("ops-report", ["report=busy"], f"⏳ {OPS}"),
    ("ops-report", ["report=idle"], f"✅ {OPS}"),
    ("ops-decide", ["decide=idle"], "✋ 🔧 new session (ops)"),
    ("ops-chat", ["chat=idle"], "✋ 🔧 new session (ops)"),
    ("ops-seb", ["seb=idle"], "✋ 🔧 Pick a base (ops)"),
]
TOPICS = {
    "report": [
        ("ai-title", "aiTitle", "Ops list"),
        ("ai-title", "aiTitle", "Ops windows in the list"),
    ],
    "seb": [("custom-title", "customTitle", "Pick a base"), ("ai-title", "aiTitle", "Base")],
}
FOLLOWED = [
    [f"⏳ {TITLE}", f"✅ {TITLE}"],
    [f"❌ {TITLE}"],
    [f"⏳ {OPS}", "⏳ 🔧 Ops agent windows (ops)", "🔧 Ops agent windows (ops)"],
]


def write_transcripts(home):
    folder = home / ".claude" / "projects" / "-workspace"
    folder.mkdir(parents=True)
    for session, answers in ANSWERS.items():
        entries = []
        for answer in answers:
            entries.append({"type": "user", "message": {"content": "go"}})
            entries.append({"type": "assistant", "message": {"content": [{"type": "thinking"}]}})
            text = {"type": "text", "text": answer}
            entries.append({"type": "assistant", "message": {"content": [text]}})
            entries.append({"type": "system", "subtype": "turn_duration"})
        for kind, key, topic in TOPICS.get(session, []):
            entries.append({"type": kind, key: topic, "sessionId": session})
        lines = "".join(f"{json.dumps(entry)}\n" for entry in entries)
        (folder / f"{session}.jsonl").write_text(lines)


def get_marks():
    """The titles of a bundle, then of ops windows: the title of the conversation, finished on a
    report that leaves nothing to Seb, the custom title over Claude's."""
    failures = []
    for name, statuses, expected in [(BUNDLE, *case) for case in MARKS] + OPS_MARKS:
        found = subprocess.run(
            [sys.executable, str(HERE / "client" / "window-title.py"), name, *statuses],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        if found != expected:
            failures.append(f"{name} {statuses}: titled {found!r}, expected {expected!r}")
    return failures


def get_loaded():
    """agent-terminator.py loads with the standard library alone, as its python3 is the system one."""
    path = HERE / "agent-terminator.py"
    load = (
        "import importlib.util as util;"
        f"spec = util.spec_from_file_location('terminator', {str(path)!r});"
        "spec.loader.exec_module(util.module_from_spec(spec))"
    )
    found = subprocess.run(
        [sys.executable, "-S", "-c", load],
        capture_output=True,
        check=False,
        text=True,
    )
    if found.returncode:
        return [f"agent-terminator.py: {found.stderr.strip().splitlines()[-1]}"]
    return []


def get_followed(home):
    """The titles agent-terminator.py gives its window while the session of its claude works,
    then ends on a verdict, and when the run fails before its claude starts. A claude of the
    bundle started before the window does not count. Then how its terminator ends on SIGUSR1,
    which agent-terminal.py sends after a takeover, and the windows it minimizes: each one once,
    as it shows. Last, an ops window opened with no task, which is not minimized: its title follows
    the title of the conversation, and loses its mark once its claude ended."""
    spec = importlib.util.spec_from_file_location("terminator", HERE / "agent-terminator.py")
    terminator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(terminator)
    sessions = home / BUNDLE / "sessions"
    sessions.mkdir(parents=True)
    wmctrl = home / "wmctrl"
    wmctrl.write_text(
        "#!/bin/sh\n"
        f'[ "$1" = -l ] && echo "0x0a 0 $(cat {home}/pid) box {TITLE}" || echo "$6" >> {home}/log\n'
    )
    wmctrl.chmod(0o755)
    agent = home / BUNDLE / ".agent"
    agent.mkdir()
    terminator.CHECK = 0.1
    terminator.CLAUDE_CONFIG = home
    terminator.WMCTRL = str(wmctrl)
    terminator.get_agent_folder = lambda bundle: agent
    minimized = []
    terminator.minimize = minimized.append

    def write(file, **record):
        (sessions / file).write_text(json.dumps({"name": BUNDLE, **record}))

    def run_claude():
        now = time.time() * 1000
        write("12.json", sessionId="seb", startedAt=now - 60000, status="waiting")
        write("13.json", sessionId="other", startedAt=now, status="waiting", name="other")
        time.sleep(0.5)
        write("14.json", sessionId="pushed", startedAt=now, status="busy")
        time.sleep(0.5)
        write("14.json", sessionId="pushed", startedAt=now, status="idle")

    def fail_to_start():
        time.sleep(0.5)
        (agent / "done").write_text("failed\n")

    titles = []
    for run in (run_claude, fail_to_start):
        window = subprocess.Popen(["sleep", "2"])
        (home / "pid").write_text(str(window.pid))
        (home / "log").write_text("")
        threading.Thread(target=run, daemon=True).start()
        terminator.follow(BUNDLE, window, time.time() * 1000 - 1000)
        titles.append((home / "log").read_text().splitlines())
        for path in sessions.iterdir():
            path.unlink()
    ops_sessions = home / "host-ctl" / "sessions"
    ops_sessions.mkdir(parents=True)
    running = [True]
    terminator.HOST_FOLDER = "/repo/host-ctl"
    terminator.REFRESH = 0.4
    terminator.is_running = lambda bundle: running[0]

    def run_ops():
        record = {"name": "repo-ab", "sessionId": "report", "startedAt": time.time() * 1000}
        (ops_sessions / "15.json").write_text(json.dumps({**record, "status": "busy"}))
        time.sleep(0.2)
        topic = {"type": "ai-title", "aiTitle": "Ops agent windows", "sessionId": "report"}
        with (home / ".claude" / "projects" / "-workspace" / "report.jsonl").open("a") as file:
            file.write(f"{json.dumps(topic)}\n")
        time.sleep(0.8)
        running[0] = False

    window = subprocess.Popen(["sleep", "2"])
    (home / "pid").write_text(str(window.pid))
    (home / "log").write_text("")
    threading.Thread(target=run_ops, daemon=True).start()
    terminator.follow("ops-report", window, time.time() * 1000 - 1000, hide=False)
    titles.append((home / "log").read_text().splitlines())
    window = subprocess.Popen(["sleep", "30"])
    threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGUSR1)).start()
    closed = terminator.follow(BUNDLE, window, time.time() * 1000)
    return titles, closed, minimized


def get_running():
    """Whether an ops window has its agent left: an agent-terminal.py process of its name, not its
    shell, and not agent-terminator.py, which holds it as COMMAND."""
    script = str(HERE / "agent-terminal.py")
    sleep = [sys.executable, "-c", "import time; time.sleep(60)"]
    check = (
        "import importlib.util as util, sys;"
        f"spec = util.spec_from_file_location('terminator', {str(HERE / 'agent-terminator.py')!r});"
        "terminator = util.module_from_spec(spec); spec.loader.exec_module(terminator);"
        "print(terminator.is_running(sys.argv[-1]))"
    )
    cases = [
        ([*sleep, script, "ops-a"], "ops-a", "True"),
        ([*sleep, script, "ops-b", "--shell"], "ops-b", "False"),
        (None, "ops-c", "False"),
    ]
    failures = []
    for command, name, expected in cases:
        agent = command and subprocess.Popen(command)
        time.sleep(0.2)
        holder = [sys.executable, "-c", check, script, name]
        found = subprocess.run(holder, capture_output=True, check=False, text=True).stdout.strip()
        if agent:
            agent.kill()
            agent.wait()
        if found != expected:
            failures.append(f"is_running {name}: {found!r}, expected {expected!r}")
    return failures


def main():
    failures = []
    with tempfile.TemporaryDirectory() as root:
        home = pathlib.Path(root)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ.update(HOME=root, USER="seb")
        write_transcripts(home)
        failures += get_marks()
        failures += get_loaded()
        failures += get_running()
        titles, closed, minimized = get_followed(home)
        for found, expected in zip(titles, FOLLOWED, strict=True):
            if found != expected:
                failures.append(f"agent-terminator.py: titled {found}, expected {expected}")
        if closed != -signal.SIGTERM:
            failures.append(f"agent-terminator.py: SIGUSR1 left its terminator on {closed}")
        if minimized != ["0x0a", "0x0a"]:
            failures.append(f"agent-terminator.py: minimized {minimized}, expected 0x0a twice")
    for line in failures:
        print(line)
    cases = len(MARKS) + len(OPS_MARKS) + len(FOLLOWED) + 6
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
