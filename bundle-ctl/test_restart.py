#!/usr/bin/env python3
"""Which agent terminal the daemon restarts on new code or closes once its agent gave its verdict,
and how it finds them, without a terminal: python3 test_restart.py
"""

import json
import math
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

REQUEUE_BASE = {**FREE_BASE, "done": False, "errors": 6, "answer": "API Error: 502"}
REQUEUE_CASES = [
    ({}, {"after": 15}),
    ({"errors": 7}, {"after": 30}),
    ({"errors": 10}, {"after": 240}),
    ({"errors": 1, "login": True}, {"login": True}),
    ({"errors": 1, "login": True, "minimized": False}, False),
    ({"done": True}, False),
    ({"errors": 5}, False),
    ({"errors": 0}, False),
    ({"queued": True}, False),
    ({"held": False}, False),
    ({"pause": True}, False),
    ({"status": "busy"}, False),
    ({"status": "shell"}, False),
    ({"minimized": False}, False),
]

LIMIT_SWITCH ={"main": {"out_until": 4 * 86400}, "backup": {"out_until": -3600, "token": True}}
LIMIT_BASE = {
    "kind": "local",
    "switch": LIMIT_SWITCH,
    "session": "s1",
    "status": "idle",
    "resets": 4 * 86400,
    "window": True,
    "minimized": True,
    "queued": False,
    "held": True,
    "pause": False,
    "ending": False,
}
BLOCKED = "Identity verification pending"
LIMIT_CASES = [
    ({}, "here"),
    ({"kind": "ops"}, "here"),
    ({"kind": "remote"}, "worker"),
    ({"kind": "remote", "window": False}, "worker"),
    ({"kind": "remote", "window": False, "minimized": False}, "worker"),
    ({"kind": "remote", "minimized": False}, False),
    ({"kind": "remote", "ending": True}, False),
    ({"kind": "ops", "minimized": False}, False),
    ({"status": "shell"}, "here"),
    ({"status": "busy"}, False),
    ({"status": "waiting"}, "here"),
    ({"status": "waiting", "resets": None}, False),
    ({"status": None}, False),
    ({"resets": None}, False),
    ({"resets": 300}, False),
    ({"minimized": False}, False),
    ({"queued": True}, False),
    ({"held": False}, False),
    ({"pause": True}, False),
    ({"switch": {**LIMIT_SWITCH, "backup": {"out_until": 3600, "token": True}}}, False),
    (
        {
            "switch": {
                **LIMIT_SWITCH,
                "backup": {"out_until": -60, "token": True, "blocked_reason": BLOCKED},
            }
        },
        False,
    ),
    (
        {"switch": {**LIMIT_SWITCH, "backup": {"out_until": -60, "refused": 1, "token": True}}},
        False,
    ),
    ({"switch": {**LIMIT_SWITCH, "backup": {"out_until": -60, "token": False}}}, False),
    ({"switch": {**LIMIT_SWITCH, "backup": {"out_until": -60}}}, False),
    ({"switch": {"main": {"out_until": -60}, "backup": {"out_until": -60}}}, "here"),
    ({"switch": None}, False),
]
LIMIT_NAMES = {"local": "l", "ops": "ops-s1", "remote": "r"}


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
    daemon.TITLE.is_resuming = lambda session, *_: any(
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
    daemon.get_transcript = lambda session, *_: transcript
    daemon.get_last_text = lambda transcript: case["answer"]
    daemon.get_session_status = lambda bundle, session: case["status"]
    daemon.TITLE.is_resuming = lambda session, *_: case["resuming"]
    daemon.read_stat = lambda pid: (pid, 0)
    daemon.is_minimized = lambda pid, env: case["minimized"]
    daemon.session_env = dict
    daemon.close_freed = closed.append
    daemon.TITLE.count_api_errors = lambda session, most, *_: min(case.get("errors", 0), most)
    daemon.TITLE.is_login_wait = lambda session, *_: case.get("login", False)

    def requeue_agent(caller, query, body, **kwargs):
        closed.append((caller, body["bundle"], kwargs))

    daemon.requeue_agent = requeue_agent
    daemon.print = lambda *args, **kwargs: None
    daemon.free_done_terminals()
    daemon.requeue_failed_terminals()
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
    for at, (changes, expected) in enumerate(REQUEUE_CASES):
        bundle = f"r{at}"
        start = time.time()
        found = free(root, bundle, {**REQUEUE_BASE, **changes})
        if found and "after" in found[0][2]:
            ahead = daemon.get_next_time(found[0][2]["after"]) - start
            found[0][2]["after"] = math.ceil(ahead / 60 - 0.02)
        if found != ([("host", bundle, expected)] if expected else []):
            failures.append(f"requeue {changes}: {found}, expected {expected}")
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


def limit(root, case, at, lines=None, fresh=True):
    """How one pass at `at` puts back in the queue the agent in `case`, of a terminal here (`l`),
    an ops window (`ops-s1`) or a worker (`r`): `here` as Back to queue does, `worker` stopped on
    its worker. The times of the case are seconds from `at`: until when an account is out, when
    the limit of the session resets. With `fresh`, no agent went back on that limit before."""
    name = LIMIT_NAMES[case["kind"]]
    agent = root / name / ".agent"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "session").write_text(f"{case['session']}\n")
    (agent / "terminal").write_text(f"{1000 if case['held'] else 1}\n")
    for kept, there in (("pause", case["pause"]), ("remote-ending", case["ending"])):
        (agent / kept).unlink(missing_ok=True)
        if there:
            (agent / kept).touch()
    status = root / "status.json"
    switch = case["switch"]
    status.write_text(
        "{"
        if switch is None
        else json.dumps(
            {
                account: {**entry, "out_until": at + entry["out_until"]}
                for account, entry in switch.items()
            }
        )
    )
    containers = {"l": "l", "ops-s1": "host-ctl", "r": "r"}
    session = name.removeprefix("ops-") if case["kind"] == "ops" else case["session"]
    done = []
    daemon.SWITCH_STATUS = status
    daemon.HOST_FOLDER = "/repo/host-ctl"
    daemon.read_state = lambda: {"queue": [{"bundle": name}] if case["queued"] else []}
    daemon.get_agent_folder = lambda bundle: root / bundle / ".agent"
    daemon.get_terminals = lambda ops=False: {name: 1000} if ops and case["window"] else {}
    daemon.get_remote_agents = lambda: {"r": "sebpc"} if name == "r" else {}
    daemon.get_session_status = lambda container, asked: (
        case["status"] if (container, asked) == (containers[name], session) else None
    )
    daemon.TITLE.get_limit_reset = lambda asked, bundle: (
        at + case["resets"] if case["resets"] and (asked, bundle) == (session, name) else None
    )
    daemon.read_stat = lambda pid: (pid + 1, 0)
    daemon.is_minimized = lambda pid, env: pid == 1001 and case["minimized"]
    daemon.session_env = dict
    daemon.requeue_agent = lambda caller, query, body, **kwargs: done.append(
        ("here", caller, body, kwargs)
    )
    daemon.requeue_remote_agent = lambda bundle: done.append(("worker", bundle))
    daemon.print = lambda *args, **kwargs: lines.append(args[0]) if lines is not None else None
    if fresh:
        daemon.limit_requeues.update(switch=None, agents=set())
    daemon.requeue_usage_limited()
    return done


def check_limit(root):
    """Each case of LIMIT_CASES, then an agent that stops on the limit again: it stays until the
    state of the switch changes, then goes back once more; another session of its bundle goes
    at once. Then the journal lines, and how an agent on a worker goes back."""
    failures = []
    host_folder, requeue_remote_agent = daemon.HOST_FOLDER, daemon.requeue_remote_agent
    at = time.time()
    for changes, expected in LIMIT_CASES:
        case = {**LIMIT_BASE, **changes}
        name = LIMIT_NAMES[case["kind"]]
        found = limit(root, case, at)
        wanted = {"here": [("here", "host", {"bundle": name}, {})], "worker": [("worker", name)]}
        if found != wanted.get(expected, []):
            failures.append(f"limit {changes}: {found}, expected {expected}")
    blocked = {
        **LIMIT_SWITCH,
        "backup": {"out_until": -60, "token": True, "blocked_reason": BLOCKED},
    }
    steps = [
        (LIMIT_BASE, 1),
        (LIMIT_BASE, 0),
        ({**LIMIT_BASE, "switch": blocked}, 0),
        (LIMIT_BASE, 1),
        (LIMIT_BASE, 0),
        ({**LIMIT_BASE, "session": "s2"}, 1),
    ]
    found = [len(limit(root, case, at, fresh=index == 0)) for index, (case, _) in enumerate(steps)]
    if found != [count for _, count in steps]:
        failures.append(f"limit again, same switch, changed, same, new session: {found}")
    lines = []
    limit(root, LIMIT_BASE, at, lines)
    limit(root, {**LIMIT_BASE, "kind": "remote"}, at, lines)
    when = daemon.format_time(at + 4 * 86400)
    expected = [
        f"l: its claude waits for the usage limit until {when}, and claude-switch has backup"
        " free: back to the queue",
        f"r: its claude on sebpc waits for the usage limit until {when}, and claude-switch has"
        " backup free: back to the queue",
    ]
    if lines != expected:
        failures.append(f"limit journal: {lines}")
    daemon.HOST_FOLDER, daemon.requeue_remote_agent = host_folder, requeue_remote_agent
    daemon.print = lambda *args, **kwargs: None
    failures += check_remote_requeue(root)
    return failures


def check_remote_requeue(root):
    """requeue_remote_agent stops the claude of the agent on its worker, then queues its session
    last; a bundle queued already is refused before anything stops."""
    calls = []
    agent = root / "w" / ".agent"
    agent.mkdir(parents=True)
    (agent / "session").write_text("s1\n")
    queue = [{"bundle": "a", "priority": 3}, {"bundle": "b", "priority": -2}]
    daemon.read_state = lambda: {"queue": queue}
    daemon.get_agent_folder = lambda bundle: root / bundle / ".agent"
    daemon.get_bundle_container = lambda bundle: None
    daemon.enqueue = lambda caller, bundle, task: calls.append(("queue", caller, bundle, task))
    end = daemon.remote_agent.end
    daemon.remote_agent.end = lambda bundle, folder: calls.append(("end", bundle, folder))
    try:
        daemon.requeue_remote_agent("w")
        queue.append({"bundle": "w"})
        try:
            daemon.requeue_remote_agent("w")
        except daemon.Refused:
            calls.append("refused")
    finally:
        daemon.remote_agent.end = end
    task = {"priority": -2, "resume": True, "task": daemon.GO_ON}
    expected = [("end", "w", root / "w"), ("queue", "host", "w", task), "refused"]
    return [] if calls == expected else [f"requeue_remote_agent: {calls}"]


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


def check_launches(root):
    """Which launches a restart of the daemon keeps: all but the spawn of a queued task whose
    terminal never started."""
    daemon.get_agent_folder = lambda bundle: root / bundle
    agents = {
        "spawned": {"task.md": 1000},
        "started": {"task.md": 1000, "terminal": 1001},
        "window": {"task.md": 500},
        "bare": {},
    }
    for bundle, files in agents.items():
        (root / bundle).mkdir()
        for name, mtime in files.items():
            (root / bundle / name).write_text("x\n")
            os.utime(root / bundle / name, (mtime, mtime))
    found = sorted(daemon.get_alive_launches(dict.fromkeys(agents, 1000)))
    return [] if found == ["bare", "started", "window"] else [f"launches after a restart: {found}"]


def main():
    with tempfile.TemporaryDirectory() as root:
        failures = check_processes(pathlib.Path(root))
    get_terminals, read_stat = daemon.get_terminals, daemon.read_stat
    with tempfile.TemporaryDirectory() as root:
        failures += check_cases(pathlib.Path(root))
    with tempfile.TemporaryDirectory() as root:
        failures += check_free(pathlib.Path(root))
    with tempfile.TemporaryDirectory() as root:
        failures += check_limit(pathlib.Path(root))
    with tempfile.TemporaryDirectory() as root:
        failures += check_launches(pathlib.Path(root))
    daemon.get_terminals, daemon.read_stat = get_terminals, read_stat
    for failure in failures:
        print(f"FAIL {failure}")
    total = len(CASES) + len(FREE_CASES) + len(REQUEUE_CASES) + len(LIMIT_CASES) + 9
    print(f"{total - len(failures)}/{total} cases as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
