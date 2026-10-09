#!/usr/bin/env python3
"""What a pass of the queue launches, keeps and drops, without podman: python3 test_queue.py"""

import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon  # noqa: E402

QUEUED = 1760000000.0
TASK = "do"

FRESH = {}
GONE = None
STARTS_IT = {"queued": f"{QUEUED}\n", "session": "s\n", "task.md": TASK, "terminal": "1\n"}
STARTS_ANOTHER = {**STARTS_IT, "queued": f"{QUEUED - 60}\n"}
NEVER_STARTED_ANOTHER = {**STARTS_ANOTHER, "old": ""}
RUNS_IT = {**STARTS_IT, "run.lock": ""}
RUNS_ANOTHER = {**RUNS_IT, "queued": f"{QUEUED - 60}\n"}
ENDED_ANOTHER = {**RUNS_ANOTHER, "done": "0\n"}
LAUNCHES_ANOTHER = {"launching": "", "task.md": "other"}
NEVER_RAN_ANOTHER = {"queued": f"{QUEUED - 60}\n", "task.md": "other"}
RUNS_ANOTHER_IN_WINDOW = {"session": "s\n", "task.md": "other", "window": ""}

CASES = [
    ({"fresh": FRESH}, ["fresh"], []),
    ({"gone": GONE, "fresh": FRESH}, ["fresh"], []),
    ({"taken": RUNS_IT, "gone": GONE, "fresh": FRESH}, ["fresh"], []),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH},
        ["fresh"],
        ["busy: its agent to end, then its terminal to close"],
    ),
    ({"ended": ENDED_ANOTHER}, [], ["ended: its terminal to close"]),
    ({"launching": LAUNCHES_ANOTHER}, [], ["launching: its agent to end"]),
    ({"stranded": NEVER_RAN_ANOTHER}, ["stranded"], []),
    ({"window": RUNS_ANOTHER_IN_WINDOW}, [], ["window: its agent to end"]),
    ({"first": FRESH, "second": FRESH}, ["first"], ["second: the agent of first to start"]),
    (
        {"launching": LAUNCHES_ANOTHER, "fresh": FRESH},
        [],
        ["launching: its agent to end", "fresh: the agent of launching to start"],
    ),
    (
        {"starting": STARTS_ANOTHER, "fresh": FRESH},
        [],
        [
            "starting: its agent to end, then its terminal to close",
            "fresh: the agent of starting to start",
        ],
    ),
    (
        {"stuck": NEVER_STARTED_ANOTHER, "fresh": FRESH},
        ["fresh"],
        ["stuck: its agent to end, then its terminal to close"],
    ),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH},
        [],
        ["busy: its agent to end, then its terminal to close", "fresh: a slot"],
        1,
    ),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH, "later": FRESH},
        ["fresh"],
        [
            "busy: its agent to end, then its terminal to close, over the cap",
            "later: the agent of fresh to start, over the cap",
        ],
        1,
        True,
    ),
]


def prepare(root, bundles, cap):
    """A daemon whose queue holds these bundles, each given as its `.agent` files, None when its
    folder is gone: its state, and the list its launches go to. `launching`, `old` and `window`
    are no file of an agent: the bundle was just launched and has no container yet, its files
    are older than a launch may take, or it is open in a VS Code window.
    """
    folders = {bundle: str(root / "master" / bundle) for bundle in bundles}
    for bundle, files in bundles.items():
        if files is None:
            continue
        agent = pathlib.Path(folders[bundle]) / ".agent"
        agent.parent.mkdir(parents=True)
        if files:
            agent.mkdir()
        for name, text in files.items():
            (agent / name).write_text(text)
            if "old" in files:
                os.utime(agent / name, (0, 0))
    state = {
        "launches": {
            bundle: time.time()
            for bundle, files in bundles.items()
            if files and "launching" in files
        },
        "queue": [
            {"bundle": bundle, "parent": "host", "queued": QUEUED, "task": TASK}
            for bundle in bundles
        ],
    }
    launched = []
    daemon.MAX_AGENTS = cap
    daemon.get_agent_folder = lambda bundle: pathlib.Path(folders[bundle]) / ".agent"
    daemon.get_bundle_container = lambda bundle: bundle
    daemon.get_open_windows = lambda: {
        folders[bundle]
        for bundle, files in bundles.items()
        if files and {"terminal", "window"} & set(files)
    }
    daemon.get_worktree_bundle_folder = folders.get
    daemon.get_worktree_container_folder = lambda: str(root)
    daemon.has_window = lambda container: "window" in bundles[container]
    daemon.open_terminal = lambda log, bundle: launched.append(bundle)
    daemon.read_state = lambda: state
    daemon.session_env = lambda: {"DISPLAY": ":0"}
    daemon.start_job = lambda caller, verb, work: work(None)
    daemon.write_state = lambda state: None
    return state, launched


def run_pass(root, bundles, cap=8, force=False):
    """One pass on a queue of these bundles, after a force-queue when `force`: the bundles it
    launches, and the ones it leaves in the queue with what each waits for.
    """
    state, launched = prepare(root, bundles, cap)
    if force:
        daemon.force_queue(daemon.HOST, {}, {})
    else:
        daemon.launch_queued()
    return launched, [
        f"{item['bundle']}: {item['waits_for']}{', over the cap' * item.get('forced', False)}"
        for item in state["queue"]
    ]


def run_client(path, *args):
    """`bctl ARGS` on the socket at `path`: its exit code and what it printed."""
    code = (
        "import pathlib, sys; sys.path.insert(0, sys.argv[1]); import bctl;"
        " bctl.SOCKETS = (pathlib.Path(sys.argv[2]),); sys.argv[1:] = sys.argv[3:]; bctl.main()"
    )
    res = subprocess.run(
        [sys.executable, "-c", code, str(HERE / "client"), str(path), *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    return res.returncode, (res.stdout + res.stderr).splitlines()


def check_clients(root):
    """Real clients on a socket of their own: a container may read the queue and not force it,
    the host may, and the answer says what started.
    """
    _state, launched = prepare(root, {"busy": RUNS_ANOTHER, "fresh": FRESH, "later": FRESH}, 1)
    daemon.identify = lambda connection: daemon.HOST
    daemon.launch_queued()
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    daemon.is_in_container = lambda connection: True
    code, lines = run_client(path, "queue")
    since = f"from host, since {daemon.format_time(QUEUED)}"
    checks.append(
        ("a container reads the queue", (code, lines[0]) == (0, "1/1 agents in a terminal"))
    )
    checks.append(
        (
            "the queue says what each task waits for",
            lines[2:4]
            == [
                f"queued 2: fresh (priority 0, {since}, waits for a slot)",
                f"queued 3: later (priority 0, {since}, waits for a slot)",
            ],
        ),
    )
    checks.append(("the queue ends on the memory", "GB available" in lines[-1]))
    code, lines = run_client(path, "force-queue")
    checks.append(
        (
            "a container cannot force the queue",
            (code, lines, launched)
            == (1, ["bctl: 403 only from the host, a container cannot ask it"], []),
        ),
    )
    daemon.is_in_container = lambda connection: False
    code, lines = run_client(path, "force-queue")
    checks.append(
        (
            "the host forces the queue",
            (code, lines[:3], launched)
            == (
                0,
                [
                    "3 queued tasks may start over the cap, one at a time",
                    "started: fresh",
                    "1/1 agents in a terminal",
                ],
                ["fresh"],
            ),
        ),
    )
    checks.append(
        (
            "a forced task says so",
            lines[4]
            == f"queued 2: later (priority 0, over the cap, {since}, waits for the agent of fresh"
            " to start)",
        ),
    )
    server.shutdown()
    server.server_close()
    return [f"clients: {name}: {lines}" for name, passed in checks if not passed], len(checks)


def main():
    failures = []
    for bundles, launched, queue, *options in CASES:
        with tempfile.TemporaryDirectory() as root:
            try:
                found = run_pass(pathlib.Path(root), bundles, *options)
            except OSError as error:
                found = repr(error)
        if found != (launched, queue):
            failures.append(f"{list(bundles)}: {found}, expected {(launched, queue)}")
    with tempfile.TemporaryDirectory() as root:
        failed, count = check_clients(pathlib.Path(root))
    failures += failed
    total = len(CASES) + count
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
