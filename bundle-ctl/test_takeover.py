#!/usr/bin/env python3
"""When client/agent-takeover.py frees a session from the claude of its terminal, with a fake
claude: python3 test_takeover.py
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent
SESSION = "s1"
CUT = [{"type": "assistant", "message": {"content": [{"type": "tool_use"}]}}]
ENDED = [{"type": "assistant", "message": {"content": [{"type": "text"}]}}]

CASES = [
    ("idle", ENDED, True),
    ("shell", ENDED, True),
    ("busy", CUT, False),
    ("busy", ENDED, False),
]


def take_over(root, status, transcript):
    """Run agent-takeover.py on a claude of this status: the .agent files left, and whether that
    claude still runs."""
    spec = importlib.util.spec_from_file_location("agent_takeover", HERE / "client" / "agent-takeover.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.AGENT = root / ".agent"
    module.CONFIG = root
    module.TURN_WAIT = 2
    module.AGENT.mkdir()
    (module.AGENT / "session").write_text(SESSION)
    (module.AGENT / "terminal").write_text("1")
    projects = root / "projects" / "-workspace"
    projects.mkdir(parents=True)
    (projects / f"{SESSION}.jsonl").write_text("".join(f"{json.dumps(e)}\n" for e in transcript))
    code = "import time; time.sleep(60)"
    claude = subprocess.Popen(["claude", "-c", code, "--session-id", SESSION], executable=sys.executable)
    threading.Thread(target=claude.wait, daemon=True).start()
    (root / "sessions").mkdir()
    record = {"pid": claude.pid, "sessionId": SESSION, "status": status}
    (root / "sessions" / f"{claude.pid}.json").write_text(json.dumps(record))
    try:
        module.main()
        return sorted(path.name for path in module.AGENT.iterdir()), claude.poll() is None
    finally:
        claude.kill()


def main():
    failures = []
    for status, transcript, restarted in CASES:
        expected = sorted(["handoff", "session"] + (["restarted"] if restarted else [])), False
        with tempfile.TemporaryDirectory() as root:
            found = take_over(pathlib.Path(root), status, transcript)
        if found != expected:
            failures.append(f"{status} after {transcript[-1]}: left {found}, expected {expected}")
    for line in failures:
        print(line)
    print(f"{len(CASES) - len(failures)}/{len(CASES)} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
