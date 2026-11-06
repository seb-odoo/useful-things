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


MODE = """
import importlib.util, pathlib, resource, sys
resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
spec = importlib.util.spec_from_file_location("takeover", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.get_mode(pathlib.Path(sys.argv[2])))
"""


def get_mode(root):
    """The mode of a transcript the host reads, a bundle's: past its last MB, in a process capped
    at 1 GB and 20 s, as 60 MB of small lines after it took 2.7 GB (security check 11-05)."""
    path = root / "S.jsonl"
    with path.open("wb") as file:
        file.write(b'{"type": "user", "permissionMode": "plan"}\n' + b"{}\n" * (20 << 20))
    takeover = HERE / "client" / "agent-takeover.py"
    try:
        done = subprocess.run(
            [sys.executable, "-c", MODE, str(takeover), str(path)],
            capture_output=True,
            check=False,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return "still reading after 20 s"
    return done.stdout.strip() or done.stderr.strip().splitlines()[-1]


def main():
    failures = []
    for status, transcript, restarted in CASES:
        expected = sorted(["handoff", "session"] + (["restarted"] if restarted else [])), False
        with tempfile.TemporaryDirectory() as root:
            found = take_over(pathlib.Path(root), status, transcript)
        if found != expected:
            failures.append(f"{status} after {transcript[-1]}: left {found}, expected {expected}")
    with tempfile.TemporaryDirectory() as root:
        if (found := get_mode(pathlib.Path(root))) != "plan":
            failures.append(f"mode past 60 MB of small lines: {found}")
    for line in failures:
        print(line)
    print(f"{len(CASES) + 1 - len(failures)}/{len(CASES) + 1} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
