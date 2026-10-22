#!/usr/bin/env python3
"""What a pass of the queue launches, keeps and drops, without podman: python3 test_queue.py"""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import socket
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
HELD_ANOTHER = {**ENDED_ANOTHER, "held": ""}
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
        ["busy: its agent to end"],
    ),
    ({"ended": ENDED_ANOTHER}, [], ["ended: its session to be idle"]),
    ({"ended": {**ENDED_ANOTHER, "window": ""}}, [], ["ended: its session to be idle"]),
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
            "starting: its agent to end",
            "fresh: the agent of starting to start",
        ],
    ),
    (
        {"stuck": NEVER_STARTED_ANOTHER, "fresh": FRESH},
        ["fresh"],
        ["stuck: its agent to end"],
    ),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH},
        [],
        ["busy: its agent to end", "fresh: a slot"],
        1,
    ),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH, "later": FRESH},
        ["fresh"],
        [
            "busy: its agent to end, over the cap",
            "later: the agent of fresh to start, over the cap",
        ],
        1,
        True,
    ),
    ({"ended": ENDED_ANOTHER, "fresh": FRESH}, ["fresh"], ["ended: its session to be idle"], 1),
    ({"held": HELD_ANOTHER, "fresh": FRESH}, ["fresh"], ["held: its session to be idle"], 1),
    ({"fresh": FRESH}, [], ["fresh: memory on the host"], 8, False, True),
    ({"fresh": FRESH}, ["fresh"], [], 8, True, True),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH},
        [],
        [
            "busy: its agent to end",
            "fresh: disk space (20 GB left, 30 needed)",
        ],
        8,
        False,
        False,
        20.4,
    ),
    (
        {"fresh": FRESH},
        [],
        ["fresh: disk space (20 GB left, 30 needed), over the cap"],
        8,
        True,
        False,
        20.4,
    ),
]

def prepare(root, bundles, cap, short=False, disk=100):
    """A daemon whose queue holds these bundles, each given as its `.agent` files, None when its
    folder is gone: its state, and the list its launches go to. `launching`, `old` and `window`
    are no file of an agent: the bundle was just launched and has no container yet, its files
    are older than a launch may take, or it is open in a VS Code window. `short`: the host is
    short of memory.
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
    daemon.DISK_STOP = 30
    daemon.HELD_DIR = root / "held"
    daemon.MAX_AGENTS = cap
    daemon.describe_memory = lambda _open_windows: {
        "available": 9.0,
        "ramping": 0,
        "room": None,
        "short": short,
        "stall": 0,
        "swap": 0,
    }
    daemon.get_agent_folder = lambda bundle: pathlib.Path(folders[bundle]) / ".agent"
    daemon.get_bundle_container = lambda bundle: bundle
    daemon.get_open_windows = lambda: {
        folders[bundle]
        for bundle, files in bundles.items()
        if files and {"terminal", "window"} & set(files) and "held" not in files
    }
    daemon.get_worktree_bundle_folder = folders.get
    daemon.get_worktree_container_folder = lambda: str(root)
    daemon.has_window = lambda container: "window" in bundles[container]
    daemon.is_held = lambda agent: (agent / "held").exists()
    daemon.open_terminal = lambda log, bundle, takeover=None: launched.append(bundle)
    daemon.read_disk_free = lambda: disk
    daemon.read_state = lambda: state
    daemon.session_env = lambda: {"DISPLAY": ":0"}
    daemon.start_job = lambda caller, verb, work, lock=None, bundle=None: work(None)
    daemon.write_state = lambda state: None
    return state, launched


def run_pass(root, bundles, cap=8, force=False, short=False, disk=100):
    """One pass on a queue of these bundles, after a force-queue when `force`: the bundles it
    launches, and the ones it leaves in the queue with what each waits for.
    """
    state, launched = prepare(root, bundles, cap, short, disk)
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
    state, launched = prepare(root, {"busy": RUNS_ANOTHER, "fresh": FRESH, "later": FRESH}, 1)
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
        (
            "a container reads the queue",
            (code, lines[0]) == (0, "1/1 agents at work in a terminal"),
        ),
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
                    "1/1 agents at work in a terminal",
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
    next(item for item in state["queue"] if item["bundle"] == "later")["parent"] = "busy"
    daemon.identify = lambda connection: "fresh"
    code, lines = run_client(path, "unqueue", "later")
    checks.append(
        (
            "a bundle cannot drop the task another one queued",
            (code, lines)
            == (1, ["bctl: 409 a bundle drops its own task or a task it queued only"]),
        ),
    )
    daemon.identify = lambda connection: "busy"
    code, lines = run_client(path, "unqueue", "later")
    checks.append(
        (
            "the bundle that queued a task drops it",
            (code, lines, [item["bundle"] for item in state["queue"]])
            == (
                0,
                [f"dropped the task of later, from busy since {daemon.format_time(QUEUED)}"],
                ["busy"],
            ),
        ),
    )
    code, lines = run_client(path, "unqueue", "later")
    checks.append(
        (
            "a task not queued cannot be dropped",
            (code, lines) == (1, ["bctl: 400 later is not queued"]),
        ),
    )
    server.shutdown()
    server.server_close()
    return [f"clients: {name}: {lines}" for name, passed in checks if not passed], len(checks)


def check_moves(root):
    """Real clients: the host forces one task over the cap, then moves another up and down,
    where it keeps its priority while that priority fits between its neighbours; a bundle moves
    only its own task or a task it queued."""
    bundles = {"busy": RUNS_ANOTHER, "a": FRESH, "b": FRESH, "c": FRESH, "d": FRESH}
    state, launched = prepare(root, bundles, 1)
    for item, priority in zip(state["queue"], (5, 0, 0, 0, -20)):
        item["priority"] = priority
    daemon.identify = lambda connection: daemon.HOST
    daemon.is_in_container = lambda connection: False
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def get_queue():
        return [
            (item["bundle"], item.get("priority", 0), item.get("forced", False))
            for item in state["queue"]
        ]

    checks = []
    code, _lines = run_client(path, "force-queue", "b")
    found = (code, launched, get_queue())
    queue = [("busy", 5, False), ("a", 0, False), ("c", 0, False), ("d", -20, False)]
    checks.append(("the host forces one task", found == (0, ["b"], queue)))
    for place, queue, name in (
        (2, [("busy", 5, False), ("c", 0, False), ("a", 0, False), ("d", -20, False)], "up"),
        (1, [("c", 5, False), ("busy", 5, False), ("a", 0, False), ("d", -20, False)], "first"),
        (4, [("busy", 5, False), ("a", 0, False), ("d", -20, False), ("c", -20, False)], "last"),
    ):
        code, _lines = run_client(path, "move", "c", str(place))
        checks.append((f"a task moves {name}", (code, get_queue()) == (0, queue)))
    daemon.identify = lambda connection: "a"
    refused = ["bctl: 409 a bundle moves its own task or a task it queued only"]
    found = (*run_client(path, "move", "c", "1"), get_queue())
    checks.append(("a bundle cannot move the task of another", found == (1, refused, queue)))
    daemon.identify = lambda connection: daemon.HOST
    for args, error in (
        (("move", "gone", "1"), "gone is not queued"),
        (("move", "c", "5"), "place: an integer from 1 to 4"),
        (("force-queue", "gone"), "gone is not queued"),
    ):
        found = run_client(path, *args)
        checks.append((f"{' '.join(args)} is refused", found == (1, [f"bctl: 400 {error}"])))
    server.shutdown()
    server.server_close()
    return [f"moves: {name}: {get_queue()}" for name, passed in checks if not passed], len(checks)


def check_disk(_root):
    """Under the floor a new bundle is refused, and the desktop hears of each 10 GB less."""
    checks = []
    daemon.DISK_STOP, daemon.DISK_WARN = 30, 60
    daemon.read_disk_free = lambda: 20.4
    refused = (
        "20 GB left on the disk, under 30: no new bundle until Seb frees space, his desktop says so"
    )
    for verb, body in (
        (daemon.create, {"base": "master", "name": "x"}),
        (daemon.fetch, {"name": "master-x--seb"}),
    ):
        try:
            verb(daemon.HOST, {}, body)
            error = None
        except daemon.Refused as refusal:
            error = str(refusal)
        checks.append((f"{verb.__name__} is refused under the floor", error == refused))
    shown = []
    daemon.notify = lambda summary, body: shown.append((summary, body.split(".")[0]))
    daemon.disk_warning.clear()
    for free in (75, 58, 52, 44, 25, 26, 49, 66, 71, 50):
        daemon.read_disk_free = lambda free=free: free
        daemon.warn_disk()
    checks.append(
        (
            "a warning at each 10 GB less, again once 10 GB over the line",
            shown
            == [
                ("58 GB left on the disk", "Under 30 GB, new bundles and agent starts will wait"),
                ("44 GB left on the disk", "Under 30 GB, new bundles and agent starts will wait"),
                (
                    "25 GB left on the disk",
                    "New bundles and agent starts wait until 30 GB are free",
                ),
                ("50 GB left on the disk", "Under 30 GB, new bundles and agent starts will wait"),
            ],
        ),
    )
    return [f"disk: {name}" for name, passed in checks if not passed], len(checks)


def check_priority(root):
    """A task an agent queues takes the priority of the agent's own task unless it gives one, and
    the started task keeps its priority for the tasks it queues in turn."""
    state, _launched = prepare(root, {"agent": {"priority": "-20\n"}, "child": FRESH}, 8)
    state["queue"] = []
    answer = daemon.enqueue("agent", "child", {"task": TASK})
    written = (root / "master" / "child" / ".agent" / "priority").read_text()
    checks = [
        ("no priority: the agent's own", (answer["priority"], written) == (-20, "-20\n")),
        (
            "a given priority stays",
            daemon.enqueue("agent", "child", {"priority": 5, "task": TASK})["priority"] == 5,
        ),
        ("the host gets 0", daemon.get_task_priority(daemon.HOST) == 0),
    ]
    return [f"priority: {name}" for name, passed in checks if not passed], len(checks)


def check_after(root):
    """A task given a time waits for it, past the cap and other tasks, and starts once it came; a
    force of the whole queue leaves it, a force of its bundle starts it."""
    state, launched = prepare(root, {"later": FRESH, "fresh": FRESH, "past": FRESH}, 8)
    state["queue"][0]["after"] = time.time() + 3600
    state["queue"][2]["after"] = time.time() - 1
    daemon.launch_queued()
    later = f"its time, {daemon.format_time(state['queue'][0]['after'])}"
    checks = [
        (
            "it waits for its time, the next starts",
            (launched, [(item["bundle"], item["waits_for"]) for item in state["queue"]])
            == (["fresh"], [("later", later), ("past", "the agent of fresh to start")]),
        ),
    ]
    get_starting, daemon.get_starting = daemon.get_starting, lambda *_args: None
    daemon.force_queue(daemon.HOST, {}, {})
    checks.append(("a force of the queue leaves it", launched == ["fresh", "past"]))
    daemon.force_queue(daemon.HOST, {}, {"bundle": "later"})
    checks.append(("a force of its bundle starts it", launched == ["fresh", "past", "later"]))
    daemon.get_starting = get_starting
    state["queue"] = []
    answer = daemon.enqueue(daemon.HOST, "later", {"after": "7:05", "task": TASK})
    start = time.localtime(state["queue"][0]["after"])
    checks.append(
        (
            "the next 7:05 of the host",
            (answer["queued"], start.tm_hour, start.tm_min)
            == (1, 7, 5)
            and 0 < state["queue"][0]["after"] - time.time() <= 24 * 3600,
        ),
    )
    for body, error in (
        ({"after": "24:00", "task": TASK}, "after: a time of the day, HH:MM"),
        ({"after": "08:00"}, "after: only with a task"),
    ):
        try:
            daemon.check_task("fresh", body)
            found = None
        except ValueError as refusal:
            found = str(refusal)
        checks.append((f"{body} is refused", found == error))
    return [f"after: {name}: {launched}" for name, passed in checks if not passed], len(checks)


def check_pause(root):
    """Real clients: a paused task waits for a manual resume, past the cap and other tasks, and a
    force of the whole queue leaves it; schedule pauses a task, gives it a time, or lets it go
    once a slot is free, and drops its force; a bundle schedules only its own task or a task it
    queued."""
    state, launched = prepare(root, {"paused": FRESH, "fresh": FRESH, "spare": FRESH}, 8)
    spare = dict(state["queue"].pop(), parent="fresh")
    state["queue"][0]["paused"] = True
    get_starting, daemon.get_starting = daemon.get_starting, lambda *_args: None

    def get_queue():
        return [
            (item["bundle"], item["waits_for"], item.get("forced", False))
            for item in state["queue"]
        ]

    daemon.launch_queued()
    queue = [("paused", "a manual resume", False)]
    checks = [("it waits, the next starts", (launched, get_queue()) == (["fresh"], queue))]
    daemon.force_queue(daemon.HOST, {}, {})
    checks.append(("a force of the queue leaves it", (launched, get_queue()) == (["fresh"], queue)))
    daemon.identify = lambda connection: daemon.HOST
    daemon.is_in_container = lambda connection: False
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["queue"].append(spare)
    daemon.MAX_AGENTS = 0
    code, lines = run_client(path, "schedule", "spare", "manual")
    queue = [queue[0], ("spare", "a manual resume", False)]
    checks.append(("a task is paused", (code, get_queue()) == (0, queue)))
    checks.append(("the queue is printed", "waits for a manual resume" in lines[2]))
    spare["forced"] = True
    code, _lines = run_client(path, "schedule", "spare", "7:05")
    start = time.localtime(spare["after"])
    queue = [queue[0], ("spare", f"its time, {daemon.format_time(spare['after'])}", False)]
    checks.append(
        (
            "a task is given a time, its force dropped",
            (code, get_queue(), start.tm_hour, start.tm_min) == (0, queue, 7, 5),
        ),
    )
    code, _lines = run_client(path, "schedule", "spare", "spot")
    queue = [queue[0], ("spare", "a slot", False)]
    checks.append(("a task waits for a slot", (code, get_queue()) == (0, queue)))
    daemon.MAX_AGENTS = 8
    code, _lines = run_client(path, "schedule", "paused", "spot")
    queue = [("spare", "the agent of paused to start", False)]
    found = (code, launched, get_queue())
    checks.append(("a paused task resumed starts", found == (0, ["fresh", "paused"], queue)))
    daemon.identify = lambda connection: "other"
    refused = ["bctl: 409 a bundle schedules its own task or a task it queued only"]
    found = run_client(path, "schedule", "spare", "manual")
    checks.append(("a bundle cannot schedule the task of another", found == (1, refused)))
    daemon.identify = lambda connection: "fresh"
    code, _lines = run_client(path, "schedule", "spare", "manual")
    queue = [("spare", "a manual resume", False)]
    checks.append(("the bundle that queued it schedules it", (code, get_queue()) == (0, queue)))
    daemon.identify = lambda connection: daemon.HOST
    for args, error in (
        (("schedule", "gone", "spot"), "gone is not queued"),
        (("schedule", "spare", "later"), "when: spot, manual or a time of the day, HH:MM"),
    ):
        found = run_client(path, *args)
        checks.append((f"{' '.join(args)} is refused", found == (1, [f"bctl: 400 {error}"])))
    server.shutdown()
    server.server_close()
    daemon.force_queue(daemon.HOST, {}, {"bundle": "spare"})
    checks.append(("a force of its bundle starts it", launched == ["fresh", "paused", "spare"]))
    daemon.get_starting = get_starting
    daemon.enqueue(daemon.HOST, "fresh", {"paused": True, "task": TASK})
    queue = [("fresh", "a manual resume", False)]
    checks.append(("a task is queued paused", get_queue() == queue))
    for body, error in (
        ({"paused": "yes", "task": TASK}, "paused: true or false"),
        ({"paused": True}, "paused: only with a task"),
    ):
        try:
            daemon.check_task("spare", body)
            found = None
        except ValueError as refusal:
            found = str(refusal)
        checks.append((f"{body} is refused", found == error))
    failed = [name for name, passed in checks if not passed]
    return [f"pause: {name}: {get_queue()} {launched}" for name in failed], len(checks)


def check_requeue(root):
    """Real clients: requeue queues the session of an agent again, last, and closes its window once
    its turn ended, or at PAUSE_WAIT; meanwhile the hook refuses its commands, and a turn cut there
    goes on told so. Only the host asks, for an agent window, or an agent for itself to wait for
    GitHub."""
    agents = {"idle": RUNS_IT, "busy": RUNS_IT, "slow": RUNS_IT, "offline": RUNS_IT, "other": FRESH}
    state, _launched = prepare(root, agents, 8)
    state["queue"] = [{**state["queue"][-1], "paused": True, "priority": -5}]
    windows = {"idle", "busy", "slow", "offline"}
    statuses = {"idle": "idle", "busy": "busy", "slow": "busy", "offline": "idle"}
    last = {
        "idle": {"type": "text", "text": "Nothing left: done."},
        "busy": {"type": "text", "text": "Nothing left: done."},
        "slow": {"type": "tool_use", "name": "Bash", "input": {"command": "sleep 600"}},
        "offline": {"type": "text", "text": "Waiting for GitHub."},
    }
    for bundle in windows:
        (daemon.get_agent_folder(bundle) / "session").write_text(f"{bundle}\n")
        records = root / "claude-config" / bundle / "sessions"
        records.mkdir(parents=True)
        record = {"sessionId": bundle, "startedAt": 2, "status": statuses[bundle]}
        (records / "1.json").write_text(json.dumps(record))
        (records / "0.json").write_text(json.dumps({**record, "startedAt": 1, "status": "idle"}))
        entry = {"type": "assistant", "entrypoint": "cli", "message": {"content": [last[bundle]]}}
        (root / f"{bundle}.jsonl").write_text(json.dumps(entry) + "\n")
    daemon.CLAUDE_CONFIG_CONTAINER = str(root / "claude-config")
    sys.modules["agents"].CLAUDE_CONFIG_CONTAINER = daemon.CLAUDE_CONFIG_CONTAINER
    daemon.PAUSE_WAIT = 3
    daemon.close_window = windows.discard
    daemon.get_transcript = lambda session: root / f"{session}.jsonl"
    daemon.get_window_pids = lambda bundle: [1] * (bundle in windows)
    spec = importlib.util.spec_from_file_location("run_hook", HERE / "client" / "run-hook.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)

    def ask_hook(bundle):
        hook.PAUSE = daemon.get_agent_folder(bundle) / "pause"
        sys.stdin, stdin = io.StringIO('{"tool_input": {"command": "ls"}}'), sys.stdin
        with contextlib.redirect_stdout(io.StringIO()) as out:
            hook.main()
        sys.stdin = stdin
        return json.loads(out.getvalue() or "{}").get("hookSpecificOutput", {})

    def wait_closed(bundle):
        deadline = time.monotonic() + 6
        while bundle in windows and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(0.2)
        return bundle not in windows

    def get_task(bundle):
        return next(item["task"] for item in state["queue"] if item["bundle"] == bundle)

    daemon.identify = lambda connection: daemon.HOST
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    code, lines = run_client(path, "requeue", "idle", "--paused")
    said = ["idle: its window closes once its turn ends, in 3 seconds at most; queued 2, waits for"]
    said[0] += " a manual resume"
    checks = [("the client queues it paused, last", (code, lines) == (0, said))]
    found = wait_closed("idle"), get_task("idle"), ask_hook("idle")
    closed = (True, daemon.GO_ON, {})
    checks.append(("an idle agent closes at once, told to go on", found == closed))
    code, _lines = run_client(path, "requeue", "busy")
    item = state["queue"][-1]
    found = item["bundle"], item["paused"], item["priority"], item["resume"]
    checks.append(("a busy agent is queued again, not paused", found == ("busy", False, -5, True)))
    answer = ask_hook("busy")
    reason = answer.get("permissionDecisionReason", "")
    found = answer.get("permissionDecision"), "window closes in" in reason
    checks.append(("the hook refuses its commands", found == ("deny", True)))
    refused = ["bctl: 409 busy is already queued"]
    checks.append(("it is queued once", run_client(path, "requeue", "busy") == (1, refused)))
    statuses["busy"] = "idle"
    record = {"sessionId": "busy", "startedAt": 2, "status": "idle"}
    (root / "claude-config" / "busy" / "sessions" / "1.json").write_text(json.dumps(record))
    found = wait_closed("busy"), get_task("busy"), ask_hook("busy")
    checks.append(("it closes once its turn ended", found == closed))
    started = time.monotonic()
    code, _lines = run_client(path, "requeue", "slow", "--paused")
    closed = wait_closed("slow")
    found = closed, time.monotonic() - started > 2.5, get_task("slow")
    checks.append(("a turn that goes on closes at the wait", found[:2] == (True, True)))
    told = "when Seb paused it, nothing you did" in found[2] and "Bash sleep 600" in found[2]
    checks.append(("and goes on told it was cut", told))
    daemon.identify = lambda connection: "offline"
    code, _lines = run_client(path, "requeue", "offline", "--github")
    item = next(item for item in state["queue"] if item["bundle"] == "offline")
    found = code, wait_closed("offline"), item["task"], item.get("github")
    checks.append(("an agent queues itself for GitHub", found == (0, True, daemon.GO_ON, True)))
    for caller, args, error in (
        ("idle", ("requeue", "busy"), "409 requeue: only the host and the ops container stop an"),
        ("idle", ("requeue", "other", "--github"), "409 requeue: only the host and the ops"),
        (daemon.HOST, ("requeue", "other"), "400 other has no agent window"),
        (daemon.HOST, ("requeue", "../x"), "400 no bundle folder for ../x"),
    ):
        daemon.identify = lambda connection, caller=caller: caller
        found = run_client(path, *args)
        refused = found[0] == 1 and found[1][0].startswith(f"bctl: {error}")
        checks.append((f"{' '.join(args)} from {caller} is refused", refused))
    server.shutdown()
    server.server_close()
    try:
        daemon.requeue_agent(daemon.HOST, {}, {"bundle": "idle", "paused": "yes"})
        found = None
    except ValueError as refusal:
        found = str(refusal)
    refused = found == "requeue: a bundle, and github or paused true or false"
    checks.append(("paused is true or false", refused))
    failed = [name for name, passed in checks if not passed]
    return [f"requeue: {name}: {state['queue']}" for name in failed], len(checks)


def check_github(root):
    """A task that waits for the GitHub connection of the host asks for it, GITHUB_ASK seconds
    apart at most, and starts once its socket is there, told so; a force of the queue leaves it,
    a force or a schedule of its bundle lets it go."""
    checks = []
    state, launched = prepare(root, {"offline": FRESH}, 8)
    state["queue"][0].update(github=True, resume=True, task=daemon.GO_ON)
    daemon.GITHUB_MUX = root / "mux"
    start = root / "mux" / "request" / "start"
    daemon.launch_queued()
    asked = start.read_text()
    daemon.force_queue(daemon.HOST, {}, {})
    found = launched, state["queue"][0]["waits_for"], start.read_text() == asked
    waits = ([], "the GitHub connection of the host", True)
    checks.append(("it waits and asks once, a force of the queue aside", found == waits))
    os.utime(start, (0, 0))
    daemon.launch_queued()
    checks.append(("it asks again later", start.read_text() != asked))
    (root / "mux" / "sock").mkdir()
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(root / "mux" / "sock" / "github"))
        daemon.launch_queued()
    task = (daemon.get_agent_folder("offline") / "task.md").read_text()
    back = task.startswith(daemon.GO_ON) and "GitHub connection of the host is back" in task
    found = launched, back
    checks.append(("it starts once the connection is up, told so", found == (["offline"], True)))
    for name, action, body in (
        ("a force of its bundle", daemon.force_queue, {"bundle": "later"}),
        ("a schedule", daemon.schedule_task, {"bundle": "later", "when": "spot"}),
    ):
        state, launched = prepare(root / name.replace(" ", "-"), {"later": FRESH}, 8)
        state["queue"][0]["github"] = True
        action(daemon.HOST, {}, body)
        checks.append((f"{name} lets it go", launched == ["later"]))
    failed = [name for name, passed in checks if not passed]
    return [f"github: {name}: {state['queue']}" for name in failed], len(checks)


def check_build(root):
    """A task queued with its build starts in the bare folder, told so, and only its own bundle
    builds it; under the disk floor such a task is still queued."""
    checks = []
    state, launched = prepare(root, {"new": GONE, "later": GONE}, 8)
    build = ["fetch_bundle.py", "new", "--no-open"]
    for item in state["queue"]:
        item["build"] = build
    daemon.prepare_folder = lambda bundle: os.makedirs(daemon.get_worktree_bundle_folder(bundle))
    daemon.launch_queued()
    task = (root / "master" / "new" / ".agent" / "task.md").read_text()
    checks.append(("the first starts", launched == ["new"]))
    checks.append(("its task says it is not built", task == f"{daemon.NOT_BUILT}\n\n{TASK}"))
    checks.append(
        (
            "the next waits, still to build",
            [(item["bundle"], item["build"], item["waits_for"]) for item in state["queue"]]
            == [("later", build, "the agent of new to start")],
        ),
    )
    try:
        daemon.build_bundle("later", {}, {"bundle": "new"})
        refusal = None
    except daemon.Refused as error:
        refusal = str(error)
    checks.append(("another bundle cannot build it", refusal == "build: a container builds its own bundle only"))
    ran = []
    daemon.run_script = lambda log, *args: ran.append(list(args))
    daemon.build_bundle("new", {}, {})
    checks.append(("its bundle builds it once", (ran, state["builds"]) == ([build], {})))
    daemon.read_disk_free = lambda: 20.4
    daemon.check_unpushed = lambda bundle, script: None
    daemon.local_branches = lambda bundle: []
    daemon.get_worktree_bundle_folder = lambda bundle: str(root / "master" / bundle)
    daemon.get_agent_folder = lambda bundle: root / "master" / bundle / ".agent"
    code, answer = daemon.create(daemon.HOST, {}, {"base": "master", "name": "x", "task": TASK})
    queued = next(item for item in state["queue"] if item["bundle"] == answer["bundle"])
    checks.append(
        (
            "under the floor a task is queued with its build",
            (code, queued["build"][0], queued["waits_for"])
            == (202, "create_bundle.py", "disk space (20 GB left, 30 needed)"),
        ),
    )
    bare = root / "bare"
    (bare / ".agent").mkdir(parents=True)
    (bare / ".claude").symlink_to(root)
    checks.append(("a folder of links and an agent is bare", daemon.is_bare(bare)))
    (bare / "tasks").mkdir()
    checks.append(("the tasks its agent queued keep it bare", daemon.is_bare(bare)))
    (bare / ".agent" / "session").write_text("s\n")
    daemon.get_worktree_bundle_folder = lambda bundle: str(root / bundle)
    daemon.get_agent_folder = lambda bundle: root / bundle / ".agent"
    daemon.get_bundle_container = lambda bundle: None
    for name, resume, expected in (("a new task", False, "refused"), ("a resume", True, 202)):
        state["queue"] = []
        try:
            found = daemon.open_bundle(daemon.HOST, {}, {"bundle": "bare", "resume": resume, "task": TASK})[0]
        except daemon.Refused:
            found = "refused"
        checks.append((f"{name} in a bare folder: {expected}", found == expected))
    (bare / "odoo").mkdir()
    checks.append(("a worktree makes it no longer bare", not daemon.is_bare(bare)))
    return [f"build: {name}" for name, passed in checks if not passed], len(checks)


def check_parked(root):
    """A parked session whose checkout went starts bare on that session, told that its window
    closed, and a resume waits while the checkout of its bundle is deleted."""
    state, launched = prepare(root, {"deleting": GONE, "parked": GONE}, 8)
    for item in state["queue"]:
        item["build"] = ["fetch_bundle.py", item["bundle"], "--no-open"]
        item.update(resume=True, session=f"{item['bundle']}-session", task=daemon.GO_ON)
    daemon.prepare_folder = lambda bundle: os.makedirs(daemon.get_worktree_bundle_folder(bundle))
    daemon.jobs["deleting"] = {"state": "running", "verb": "delete deleting"}
    try:
        daemon.launch_queued()
    finally:
        daemon.jobs.pop("deleting")
    agent = root / "master" / "parked" / ".agent"
    told = f"{daemon.NOT_BUILT}\n\n{daemon.RESUMED.format(time=daemon.format_time(QUEUED))}"
    waits = [(item["bundle"], item["waits_for"]) for item in state["queue"]]
    checks = [
        ("the parked session starts", launched == ["parked"]),
        ("on its own session", daemon.read_text(agent / "session") == "parked-session"),
        ("told it is bare and its window closed", daemon.read_text(agent / "task.md") == told),
        ("a resume waits for its checkout to go", waits == [("deleting", "the deletion of its checkout")]),
    ]
    return [f"parked: {name}" for name, passed in checks if not passed], len(checks)


def check_hand_over(root):
    """A task behind an agent that gave its verdict in a terminal goes to that terminal once its
    session is idle, minimized or not, with no new window and no card in hok: the files of the
    terminal and of its PR watch stay, and so do the model and the effort of the session. A turn,
    a dialog or a background command that runs, a time to wait and a task for another session keep
    it waiting."""
    ended = {**ENDED_ANOTHER, "container": "c\n", "mode": "auto\n", "model": "sonnet\n"}
    bundles = {
        "ended": {**ended, "prwatch.json": "{}"},
        "asking": RUNS_ANOTHER,
        "typing": ended,
        "dialog": ended,
        "watching": ended,
        "later": ended,
        "other": ended,
    }
    state, launched = prepare(root, bundles, 8)
    after = time.time() + 3600
    for item in state["queue"]:
        item["model"] = "haiku"
        if item["bundle"] == "later":
            item["after"] = after
        if item["bundle"] == "other":
            item["session"] = "x"
    statuses = {"typing": "busy", "dialog": "waiting", "watching": "shell"}
    status = daemon.get_session_status
    daemon.get_session_status = lambda bundle, session: statuses.get(bundle, "idle")
    daemon.launch_safely()
    agent = daemon.get_agent_folder("ended")
    kept = sorted(path.name for path in agent.iterdir())
    history = sorted(path.name for path in next((agent / "history").iterdir()).iterdir())
    waits = [f"{item['bundle']}: {item['waits_for']}" for item in state["queue"]]
    daemon.get_session_status = status
    checks = [
        ("no window opens", launched == []),
        (
            "the session, its terminal and its PR watch stay",
            kept
            == ["container", "history", "mode", "model", "parent", "priority", "prwatch.json",
                "queued", "session", "task.md", "terminal"],
        ),
        ("its verdict and its run go to the history", {"done", "run.lock"} <= set(history)),
        ("the task waits to be typed", daemon.read_text(agent / "task.md") == TASK),
        ("the model of the session stays", daemon.read_text(agent / "model") == "sonnet"),
        (
            "the others wait",
            waits
            == [
                "asking: its agent to end",
                "typing: its session to be idle",
                "dialog: its session to be idle",
                "watching: its session to be idle",
                f"later: its time, {daemon.format_time(after)}",
                "other: its terminal to close",
            ],
        ),
        ("hok gets no card", not any(daemon.HELD_DIR.glob("*.json"))),
    ]
    return [f"hand over: {name}" for name, passed in checks if not passed], len(checks)


def check_ramp(root):
    """Only an agent that began its run in the last AGENT_RAMP seconds and gave no verdict counts
    for the memory it takes soon.
    """
    old = time.time() - daemon.AGENT_RAMP - 1
    for name, files in (("fresh", ()), ("old", ()), ("ended", ("done",))):
        agent = root / name / ".agent"
        (agent / "run.lock").mkdir(parents=True)
        for file in files:
            (agent / file).touch()
        if name == "old":
            os.utime(agent / "run.lock", (old, old))
    folders = [str(root / name) for name in ("fresh", "old", "ended", "never")]
    found = daemon.count_ramping(folders)
    return [] if found == 1 else [f"ramp: {found} agents counted, expected 1"], 1


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
    total = len(CASES)
    for check in (
        check_clients,
        check_moves,
        check_disk,
        check_after,
        check_pause,
        check_requeue,
        check_github,
        check_build,
        check_parked,
        check_priority,
        check_hand_over,
        check_ramp,
    ):
        with tempfile.TemporaryDirectory() as root:
            failed, count = check(pathlib.Path(root))
        failures += failed
        total += count
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
