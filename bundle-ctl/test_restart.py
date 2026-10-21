#!/usr/bin/env python3
"""Which agent terminal the daemon restarts on new code or closes once its agent gave its verdict,
and how it finds them, without a terminal: python3 test_restart.py
"""

import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon

BASE = {
    "started": 100,
    "held": True,
    "pause": False,
    "status": "idle",
    "resuming": False,
    "minimized": True,
}
# What differs from BASE, and whether the terminal restarts. The code changed at 200.
CASES = [
    ({}, True),
    ({"started": 300}, False),
    ({"held": False}, False),
    ({"pause": True}, False),
    ({"status": "busy"}, False),
    ({"status": "shell"}, False),
    ({"status": None}, False),
    ({"resuming": True}, False),
    ({"minimized": False}, False),
]

FREE_BASE = {
    "queued": False,
    "held": True,
    "done": True,
    "pause": False,
    "age": 600,
    "status": "idle",
    "resuming": False,
    "answer": "pushed: 1234abc\n\nDone ...",
    "minimized": True,
}
# What differs from FREE_BASE, and whether the terminal closes.
FREE_CASES = [
    ({}, True),
    ({"answer": "**needs Seb:** review odoo#1, approve"}, True),
    ({"queued": True}, False),
    ({"held": False}, False),
    ({"done": False}, False),
    ({"pause": True}, False),
    ({"age": 60}, False),
    ({"status": "busy"}, False),
    ({"status": "waiting"}, False),
    ({"status": "shell"}, False),
    ({"resuming": True}, False),
    ({"answer": "The CI is green now."}, False),
    ({"answer": None}, False),
    ({"minimized": False}, False),
]


def restart(root, terminals):
    """The bundles restarted by one call, for terminals {bundle: case}, each with the line its new
    terminal shows."""
    opened = []
    pids = {bundle: 1000 + at for at, bundle in enumerate(terminals)}
    for bundle, case in terminals.items():
        agent = root / bundle / ".agent"
        agent.mkdir(parents=True, exist_ok=True)
        (agent / "session").write_text("s1\n")
        (agent / "terminal").write_text(f"{pids[bundle] if case['held'] else 1}\n")
        (agent / "pause").unlink(missing_ok=True)
        if case["pause"]:
            (agent / "pause").touch()
    by_pid = {pids[bundle]: case for bundle, case in terminals.items()}
    daemon.get_agent_folder = lambda bundle: root / bundle / ".agent"
    daemon.get_terminals = lambda: pids
    daemon.read_stat = lambda pid: (pid, by_pid[pid]["started"])
    daemon.get_session_status = lambda bundle, session: terminals[bundle]["status"]
    daemon.TITLE.is_resuming = lambda session: any(
        case["resuming"] for case in terminals.values()
    )
    daemon.is_minimized = lambda pid, env: by_pid[pid]["minimized"]
    daemon.session_env = dict
    daemon.open_terminal = lambda log, bundle, takeover: opened.append((bundle, takeover))
    daemon.print = lambda *args, **kwargs: None
    daemon.restart_old_terminal()
    return opened


def check_cases(root):
    failures = []
    code = root / "code.py"
    code.touch()
    os.utime(code, (200, 200))
    daemon.TERMINAL_CODE = [code]
    for at, (changes, expected) in enumerate(CASES):
        daemon.restarted.clear()
        bundle = f"b{at}"
        found = [name for name, _ in restart(root, {bundle: {**BASE, **changes}})]
        if found != ([bundle] if expected else []):
            failures.append(f"{changes}: restarted {found}, expected {expected}")
    daemon.restarted.clear()
    both = {"x1": BASE, "x2": BASE}
    found = [[name for name, _ in restart(root, both)] for _ in range(3)]
    if found != [["x1"], ["x2"], []]:
        failures.append(f"two old terminals, three calls: {found}, expected one each, then none")
    daemon.restarted.clear()
    line = restart(root, {"y1": BASE})[0][1]
    if line != f"restarts on the bundle-ctl code of {daemon.format_time(200)}, same session":
        failures.append(f"the line of the new terminal: {line}")
    return failures


def free(root, bundle, case):
    """The bundles one call closes, for a terminal of `bundle` in `case`."""
    agent = root / bundle / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "session").write_text("s1\n")
    (agent / "terminal").write_text(f"{1000 if case['held'] else 1}\n")
    for name in ("done", "pause"):
        (agent / name).unlink(missing_ok=True)
        if case[name]:
            (agent / name).write_text("0\n")
    transcript = root / bundle / "s1.jsonl"
    transcript.touch()
    os.utime(transcript, (time.time() - case["age"],) * 2)
    queue = [{"bundle": bundle}] if case["queued"] else []
    closed = []
    daemon.read_state = lambda: {"queue": queue}
    daemon.get_agent_folder = lambda bundle: root / bundle / ".agent"
    daemon.get_terminals = lambda: {bundle: 1000}
    daemon.get_transcript = lambda session: transcript
    daemon.get_last_text = lambda transcript: case["answer"]
    daemon.get_session_status = lambda bundle, session: case["status"]
    daemon.TITLE.is_resuming = lambda session: case["resuming"]
    daemon.read_stat = lambda pid: (pid, 0)
    daemon.is_minimized = lambda pid, env: case["minimized"]
    daemon.session_env = dict
    daemon.close_freed = closed.append
    daemon.print = lambda *args, **kwargs: None
    daemon.free_done_terminals()
    for thread in threading.enumerate():
        if thread is not threading.main_thread():
            thread.join(1)
    return closed


def check_free(root):
    failures = []
    close_freed = daemon.close_freed
    for at, (changes, expected) in enumerate(FREE_CASES):
        bundle = f"f{at}"
        found = free(root, bundle, {**FREE_BASE, **changes})
        if found != ([bundle] if expected else []):
            failures.append(f"free {changes}: closed {found}, expected {expected}")
    daemon.close_freed = close_freed
    pause = root / "g1" / ".agent" / "pause"
    pause.parent.mkdir(parents=True)
    seen = []
    daemon.close_window = lambda bundle: seen.append(pause.exists())
    daemon.get_window_pids = lambda bundle: []
    daemon.close_freed("g1")
    if seen != [True] or pause.exists():
        failures.append(f"close_freed: pause while closing {seen}, after {pause.exists()}")
    return failures


def check_processes(root):
    """get_terminals and read_stat on processes named as agent-terminal.py runs."""
    script = root / "agent-terminal.py"
    script.write_text("import time\ntime.sleep(30)\n")
    before = time.time()
    procs = [subprocess.Popen([sys.executable, str(script), name]) for name in ("b1", "ops-s1")]
    try:
        time.sleep(0.5)
        found = {
            bundle: pid for bundle, pid in daemon.get_terminals().items() if bundle in ("b1", "ops-s1")
        }
        failures = [] if found == {"b1": procs[0].pid} else [f"get_terminals: {found}"]
        parent, started = daemon.read_stat(procs[0].pid)
        if parent != os.getpid() or not before - 1 <= started <= time.time():
            failures.append(f"read_stat: {parent}, {started}, expected {os.getpid()}, ~{before}")
        return failures
    finally:
        for proc in procs:
            proc.kill()
            proc.wait()


def main():
    with tempfile.TemporaryDirectory() as root:
        failures = check_processes(pathlib.Path(root))
    get_terminals, read_stat = daemon.get_terminals, daemon.read_stat
    with tempfile.TemporaryDirectory() as root:
        failures += check_cases(pathlib.Path(root))
    with tempfile.TemporaryDirectory() as root:
        failures += check_free(pathlib.Path(root))
    daemon.get_terminals, daemon.read_stat = get_terminals, read_stat
    for failure in failures:
        print(f"FAIL {failure}")
    total = len(CASES) + len(FREE_CASES) + 5
    print(f"{total - len(failures)}/{total} cases as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
