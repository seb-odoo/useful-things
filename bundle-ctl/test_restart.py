#!/usr/bin/env python3
"""Which agent terminal the daemon restarts on new code, and how it finds them, without a terminal:
python3 test_restart.py
"""

import os
import pathlib
import subprocess
import sys
import tempfile
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


def restart(root, terminals):
    """The bundles restarted by one call, for terminals {bundle: case}."""
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
    daemon.open_terminal = lambda log, bundle: opened.append(bundle)
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
        found = restart(root, {bundle: {**BASE, **changes}})
        if found != ([bundle] if expected else []):
            failures.append(f"{changes}: restarted {found}, expected {expected}")
    daemon.restarted.clear()
    both = {"x1": BASE, "x2": BASE}
    found = [restart(root, both), restart(root, both), restart(root, both)]
    if found != [["x1"], ["x2"], []]:
        failures.append(f"two old terminals, three calls: {found}, expected one each, then none")
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
    daemon.get_terminals, daemon.read_stat = get_terminals, read_stat
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"{len(CASES) + 3 - len(failures)}/{len(CASES) + 3} cases as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
