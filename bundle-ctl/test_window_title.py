#!/usr/bin/env python3
"""The mark of what Claude does in the title of a bundle window, and agent-terminator.py following
it with a fake wmctrl: python3 test_window_title.py"""

import importlib.util
import json
import os
import pathlib
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
    "pushed": ["pushed: c4d3b85\n\nDone"],
    "ready": ["ready to push"],
    "seb": ["needs Seb: which base?"],
}
MARKS = [
    ([], TITLE),
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
FOLLOWED = [f"⏳ {TITLE}", f"✅ {TITLE}"]


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
        lines = "".join(f"{json.dumps(entry)}\n" for entry in entries)
        (folder / f"{session}.jsonl").write_text(lines)


def get_marks():
    failures = []
    for statuses, expected in MARKS:
        found = subprocess.run(
            [sys.executable, str(HERE / "client" / "window-title.py"), BUNDLE, *statuses],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        if found != expected:
            failures.append(f"{statuses}: titled {found!r}, expected {expected!r}")
    return failures


def get_followed(home):
    """The titles agent-terminator.py gives its window while the session of its claude works,
    then ends on a verdict. A claude of the bundle started before the window does not count."""
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
    terminator.CHECK = 0.1
    terminator.CLAUDE_CONFIG = home
    terminator.WMCTRL = str(wmctrl)

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

    window = subprocess.Popen(["sleep", "2"])
    (home / "pid").write_text(str(window.pid))
    threading.Thread(target=run_claude, daemon=True).start()
    terminator.follow(BUNDLE, window, time.time() * 1000 - 1000)
    return (home / "log").read_text().splitlines()


def main():
    failures = []
    with tempfile.TemporaryDirectory() as root:
        home = pathlib.Path(root)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ.update(HOME=root, USER="seb")
        write_transcripts(home)
        failures += get_marks()
        found = get_followed(home)
        if found != FOLLOWED:
            failures.append(f"agent-terminator.py: titled {found}, expected {FOLLOWED}")
    for line in failures:
        print(line)
    cases = len(MARKS) + 1
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
