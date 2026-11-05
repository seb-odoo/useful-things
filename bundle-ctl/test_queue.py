#!/usr/bin/env python3
"""What a pass of the queue launches, keeps and drops, without podman: python3 test_queue.py"""

import contextlib
import http.server
import importlib.util
import io
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon  # noqa: E402

START_JOB = daemon.start_job
CHECK_UNPUSHED = daemon.check_unpushed
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
BUSY = "the previous agent of the bundle to end"
BUSY_SINCE = f"{BUSY} (at work since T)"
IDLE = "the previous agent of the bundle to be idle"
SINCE = re.compile(r"(?<=at work since )[^)]*")

CASES = [
    ({"fresh": FRESH}, ["fresh"], []),
    ({"gone": GONE, "fresh": FRESH}, ["fresh"], []),
    ({"taken": RUNS_IT, "gone": GONE, "fresh": FRESH}, ["fresh"], []),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH},
        ["fresh"],
        [f"busy: {BUSY_SINCE}"],
    ),
    ({"ended": ENDED_ANOTHER}, [], [f"ended: {IDLE}"]),
    ({"ended": {**ENDED_ANOTHER, "window": ""}}, [], [f"ended: {IDLE}"]),
    ({"launching": LAUNCHES_ANOTHER}, [], [f"launching: {BUSY}"]),
    ({"stranded": NEVER_RAN_ANOTHER}, ["stranded"], []),
    ({"window": RUNS_ANOTHER_IN_WINDOW}, [], [f"window: {BUSY}"]),
    ({"first": FRESH, "second": FRESH}, ["first"], ["second: the agent of first to start"]),
    (
        {"launching": LAUNCHES_ANOTHER, "fresh": FRESH},
        [],
        [f"launching: {BUSY}", "fresh: the agent of launching to start"],
    ),
    (
        {"starting": STARTS_ANOTHER, "fresh": FRESH},
        [],
        [
            f"starting: {BUSY}",
            "fresh: the agent of starting to start",
        ],
    ),
    ({"dead": {**STARTS_ANOTHER, "dead": ""}, "fresh": FRESH}, ["fresh"], [f"dead: {BUSY}"]),
    (
        {"stuck": NEVER_STARTED_ANOTHER, "fresh": FRESH},
        ["fresh"],
        [f"stuck: {BUSY}"],
    ),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH},
        [],
        [f"busy: {BUSY_SINCE}", "fresh: a slot"],
        1,
    ),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH, "later": FRESH},
        ["fresh"],
        [
            f"busy: {BUSY_SINCE}, over the cap",
            "later: the agent of fresh to start, over the cap",
        ],
        1,
        True,
    ),
    ({"ended": ENDED_ANOTHER, "fresh": FRESH}, ["fresh"], [f"ended: {IDLE}"], 1),
    ({"held": HELD_ANOTHER, "fresh": FRESH}, ["fresh"], [f"held: {IDLE}"], 1),
    ({"fresh": FRESH}, [], ["fresh: memory on the host"], 8, False, True),
    ({"fresh": FRESH}, ["fresh"], [], 8, True, True),
    ({"fresh": FRESH}, [], ["fresh: the CPU of the host"], 8, False, False, 100, True),
    ({"fresh": FRESH}, [], ["fresh: memory on the host"], 8, False, True, 100, True),
    ({"fresh": FRESH}, ["fresh"], [], 8, True, False, 100, True),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH},
        [],
        [f"busy: {BUSY_SINCE}", "fresh: a slot"],
        1,
        False,
        False,
        100,
        True,
    ),
    (
        {"busy": RUNS_ANOTHER, "fresh": FRESH},
        [],
        [
            f"busy: {BUSY_SINCE}",
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

def prepare(root, bundles, cap, short=False, disk=100, busy=False):
    """A daemon whose queue holds these bundles, each given as its `.agent` files, None when its
    folder is gone: its state, and the list its launches go to. `launching`, `old`, `window` and
    `dead` are no file of an agent: the bundle was just launched and has no container yet, its
    files are older than a launch may take, it is open in a VS Code window, or its terminal was
    killed. A `terminal` lives while its window is open, `held` with none. `short`: the host is
    short of memory; `busy`: its CPU is.
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
    daemon.PARENTS_DIR = root / "parents"
    daemon.describe_memory = lambda _open_windows: {
        "available": 9.0,
        "busy": busy,
        "cpu": 0,
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
    daemon.is_held = lambda agent: (
        (agent / "held").exists()
        or (
            (agent / "terminal").exists()
            and not (agent / "dead").exists()
            and str(agent.parent) in daemon.get_open_windows()
        )
    )
    daemon.open_terminal = lambda log, bundle, takeover=None: launched.append(bundle)
    daemon.read_disk_free = lambda: disk
    daemon.read_state = lambda: state
    daemon.session_env = lambda: {"DISPLAY": ":0"}
    daemon.start_job = lambda caller, verb, work, lock=None, bundle=None: work(None)
    daemon.write_state = lambda state: None
    return state, launched


def run_pass(root, bundles, cap=8, force=False, short=False, disk=100, busy=False):
    """One pass on a queue of these bundles, after a force-queue when `force`: the bundles it
    launches, and the ones it leaves in the queue with what each waits for.
    """
    state, launched = prepare(root, bundles, cap, short, disk, busy)
    if force:
        daemon.force_queue(daemon.HOST, {}, {})
    else:
        daemon.launch_queued()
    return launched, [
        SINCE.sub("T", f"{item['bundle']}: {item['waits_for']}")
        + ", over the cap" * item.get("forced", False)
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


def check_disk(root):
    """Under the floor a new bundle is refused, and the desktop hears of each 10 GB less, once
    across a restart of the daemon."""
    checks = []
    daemon.DISK_STOP, daemon.DISK_WARN = 30, 60
    daemon.read_disk_free = lambda: 20.4
    refused = (
        "20 GB left on the disk, under 30: no new bundle until Seb frees space, his desktop says so."
        " bctl free-disk asks his yes to free it"
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
    daemon.DISK_WARNED = root / "disk-warned"
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
    shown.clear()
    daemon.DISK_WARNED.write_text("2\n")
    for free in (29, 21, 19):
        daemon.read_disk_free = lambda free=free: free
        daemon.warn_disk()
    checks.append(
        (
            "a level warned before a restart is not warned again",
            [summary for summary, _held in shown] == ["19 GB left on the disk"],
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
    (root / "master" / "agent" / ".agent" / "priority").write_text("100000\n")
    found = daemon.get_task_priority("agent")
    checks.append(("one the bundle wrote stays in bounds", found == daemon.MAX_PRIORITY))
    return [f"priority: {name}" for name, passed in checks if not passed], len(checks)


def check_label(root):
    """The owner of a runbot label starts as a GitHub login does, never with a dash."""
    check_disk = daemon.check_disk

    def stop():
        raise daemon.Refused("past the label")

    daemon.check_disk = stop
    try:
        daemon.fetch(daemon.HOST, {}, {"name": "-x:master-y--seb"})
    except (ValueError, daemon.Refused) as error:
        found = str(error)
    finally:
        daemon.check_disk = check_disk
    if found != "not a runbot label: -x:master-y--seb":
        return [f"label: {found}"], 1
    return [], 1


def check_links(root):
    """A new task follows no link the bundle left in its .agent folder: the files of the last task
    go to a history folder of its own, and the new ones replace the links."""
    outside = root / "outside"
    outside.mkdir()
    (outside / "file").write_text("kept\n")
    prepare(root, {"linked": {"session": "s\n", "task.md": "old"}}, 8)
    agent = root / "master" / "linked" / ".agent"
    (agent / "history").symlink_to(outside)
    daemon.write_task({"bundle": "linked", "parent": "host", "queued": QUEUED, "task": TASK})
    (agent / "parent").unlink()
    (agent / "parent").symlink_to(outside / "file")
    daemon.write_task({"bundle": "linked", "parent": "host", "queued": QUEUED, "task": TASK})
    history = agent / "history"
    checks = [
        ("nothing lands where a link goes", [path.name for path in outside.iterdir()] == ["file"]),
        ("the file a link names is not written", (outside / "file").read_text() == "kept\n"),
        ("the last task goes to its history", history.is_dir() and not history.is_symlink()),
        ("a link becomes the file", (agent / "parent").read_text() == "host\n"),
    ]
    return [f"links: {name}" for name, passed in checks if not passed], len(checks)


def check_stranded_link(root):
    """The pass that queues again a task whose agent never started reads task.md as a file of the
    bundle: a link there to a host file queues nothing (security check 11-03, B1)."""
    secret = root / "secret"
    secret.write_text("the host's login\n")
    state, _launched = prepare(root, {"stranded": NEVER_RAN_ANOTHER, "big": NEVER_RAN_ANOTHER}, 0)
    state["queue"] = []
    agent = root / "master" / "stranded" / ".agent"
    (agent / "task.md").unlink()
    (agent / "task.md").symlink_to(secret)
    (root / "master" / "big" / ".agent" / "task.md").write_text("x" * (daemon.MAX_TASK + 1))
    daemon.launch_queued()
    texts = [item["task"] for item in state["queue"]]
    checks = [
        ("a link at task.md queues nothing", [item["bundle"] for item in state["queue"]] == []),
        ("its target is read by no one", not any("login" in text for text in texts)),
    ]
    return [f"stranded: {name}" for name, passed in checks if not passed], len(checks)


def check_outside(root):
    """A bundle on a PR of an author outside odoo makes bundles that are marked too, and a fetch
    it asks waits for Seb's yes, its card naming that PR (security check 11-03, B6)."""
    state, launched = prepare(root, {}, 8)
    daemon.HELD_DIR = root / "held"
    daemon.prepare_folder = lambda bundle: os.makedirs(daemon.get_worktree_bundle_folder(bundle))
    daemon.outside.OWN = root / "claude-config"
    daemon.read_disk_free = lambda: 100
    daemon.check_unpushed = lambda bundle, script: None
    daemon.local_branches = lambda bundle: []
    daemon.get_worktree_bundle_folder = lambda bundle: str(root / "master" / bundle)
    daemon.get_agent_folder = lambda bundle: root / "master" / bundle / ".agent"
    check_fetch, daemon.outside.check_fetch = daemon.outside.check_fetch, lambda *args: None
    sender, pr = "master-x--seb", {"url": "https://github.com/odoo/odoo/pull/1", "author": "ext"}
    (root / "claude-config" / sender).mkdir(parents=True)
    (root / "claude-config" / sender / "outside").write_text(json.dumps(pr))
    try:
        daemon.create(sender, {}, {"base": "master", "name": "child", "task": TASK})
        held = daemon.fetch(sender, {}, {"name": "master-mine--seb", "task": TASK})[1]
        daemon.fetch(daemon.HOST, {}, {"name": "master-host--seb", "task": TASK})
    finally:
        daemon.outside.check_fetch = check_fetch
    mark = daemon.outside.read_mark("master-child--seb") or {}
    task = root / "master" / "master-child--seb" / ".agent" / "task.md"
    card = json.loads((root / "held" / f"{held['held'][1:]}.json").read_text())
    checks = [
        ("a bundle it creates is marked", (mark.get("url"), mark.get("via")) == (pr["url"], sender)),
        ("and its task names the sender", daemon.OUTSIDE_TASK.format(bundle=sender) in task.read_text()),
        ("a fetch it asks is held", "master-mine--seb" not in launched and "held" in held),
        ("its card names the PR", pr["url"] in card["lines"][0]),
        ("a fetch of the host is not", "master-host--seb" in [
            *launched, *(item["bundle"] for item in state["queue"])]),
    ]
    return [f"outside: {name}" for name, passed in checks if not passed], len(checks)


def check_second_task(root):
    """A second task for a queued bundle is taken behind the first, even at a higher priority, and
    waits for it to start, then for the agent it started to end. A resume, or a task for a bundle
    still to build, is queued once. The bundle that queued the second task drops it, not the
    first."""
    state, launched = prepare(root, {"busy": RUNS_ANOTHER, "fresh": FRESH}, 1)
    state["queue"] = []
    daemon.enqueue(daemon.HOST, "fresh", {"priority": -15, "task": "first"})
    answer = daemon.enqueue("other", "fresh", {"priority": 5, "task": "second"})
    since = daemon.format_time(state["queue"][0]["queued"])
    waits = f"the task queued before it for the bundle to start (from host, since {since})"
    checks = [
        ("it is taken, behind the first", (answer["queued"], answer["waits_for"]) == (2, waits)),
        (
            "in the order they came",
            [item["task"] for item in state["queue"]] == ["first", "second"],
        ),
    ]
    for name, body, build in (
        ("a resume", {"resume": True, "task": "x"}, None),
        ("a task for a bundle to build", {"task": "x"}, ["create_bundle.py"]),
    ):
        state["queue"][0]["build"] = build
        try:
            daemon.check_task("fresh", body)
            found = None
        except daemon.Refused as refusal:
            found = str(refusal)
        checks.append((f"{name} is queued once", found == "fresh is already queued"))
    state["queue"][0].pop("build")
    code, dropped = daemon.unqueue("other", {}, {"bundle": "fresh"})
    found = code, dropped["parent"], [item["task"] for item in state["queue"]]
    checks.append(("its bundle drops the second task", found == (200, "other", ["first"])))
    daemon.enqueue("other", "fresh", {"priority": 5, "task": "second"})
    daemon.MAX_AGENTS = 2
    daemon.launch_queued()
    agent = root / "master" / "fresh" / ".agent"
    found = (
        launched,
        daemon.read_text(agent / "task.md"),
        [item["waits_for"] for item in state["queue"]],
    )
    checks.append(("the first starts, the second waits", found == (["fresh"], "first", [BUSY])))
    return [f"second task: {name}" for name, passed in checks if not passed], len(checks)


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
    daemon.get_transcript = lambda session, *_: root / f"{session}.jsonl"
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


def check_ops_requeue(root):
    """Real clients: an ops agent window goes back to the queue as the agent of a bundle does, its
    turn read in the sessions of the ops container; its session opens again in an ops agent window
    once resumed, its window closed and a slot free. A terminal opened on it drops its task."""
    state, launched = prepare(root, {"b": RUNS_IT}, 8)
    state["queue"] = []
    session = "11111111-2222-3333-4444-555555555555"
    ops = f"{daemon.OPS}{session}"
    folder_of = daemon.get_agent_folder
    daemon.get_agent_folder = lambda bundle: (
        root / "ops" / bundle.removeprefix(daemon.OPS)
        if bundle.startswith(daemon.OPS)
        else folder_of(bundle)
    )
    host_folder, daemon.HOST_FOLDER = daemon.HOST_FOLDER, str(root / "repo")
    records = root / "claude-config" / "repo" / "sessions"
    records.mkdir(parents=True)
    record = {"sessionId": session, "startedAt": 2, "status": "busy"}
    (records / "1.json").write_text(json.dumps(record))
    daemon.CLAUDE_CONFIG_CONTAINER = str(root / "claude-config")
    sys.modules["agents"].CLAUDE_CONFIG_CONTAINER = daemon.CLAUDE_CONFIG_CONTAINER
    entry = {"type": "assistant", "entrypoint": "cli", "message": {"content": [{"type": "text"}]}}
    entry["message"]["content"][0]["text"] = "Nothing left: done."
    (root / f"{session}.jsonl").write_text(json.dumps(entry) + "\n")
    windows = {ops}
    running, daemon.is_agent_running = daemon.is_agent_running, windows.__contains__
    daemon.PAUSE_WAIT = 3
    daemon.close_window = windows.discard
    daemon.get_transcript = lambda name, *_: next(root.glob(f"{name}.jsonl"), None)
    daemon.get_window_pids = lambda bundle: [1] * (bundle in windows)
    daemon.identify = lambda connection: daemon.HOST
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    code, lines = run_client(path, "requeue", ops, "--paused")
    found = code, lines[-1].endswith("queued 1, waits for a manual resume")
    checks = [("the client queues it paused", found == (0, True))]
    item = state["queue"][0]
    found = item["bundle"], item["paused"], item["resume"], item["session"]
    checks.append(("its session is queued", found == (ops, True, True, session)))
    time.sleep(1.5)
    checks.append(("it stays open while its turn runs", ops in windows))
    (records / "1.json").write_text(json.dumps({**record, "status": "idle"}))
    deadline = time.monotonic() + 4
    while ops in windows and time.monotonic() < deadline:
        time.sleep(0.1)
    time.sleep(0.2)
    found = ops in windows, item["task"]
    checks.append(("it closes once its turn ended, told to go on", found == (False, daemon.GO_ON)))
    daemon.launch_queued()
    checks.append(("paused, it stays in the queue", (launched, len(state["queue"])) == ([], 1)))
    daemon.schedule_task(daemon.HOST, {}, {"bundle": ops, "when": "spot"})
    task = (daemon.get_agent_folder(ops) / "task.md").read_text()
    found = (
        launched,
        state["queue"],
        task.startswith(daemon.GO_ON),
        "Seb closed your window" in task,
    )
    checks.append(("a resume opens its window on its session", found == ([ops], [], True, True)))
    windows.add(ops)
    (records / "1.json").write_text(json.dumps(record))
    daemon.MAX_AGENTS = 1
    answer = daemon.requeue_agent(daemon.HOST, {}, {"bundle": ops})[1]
    checks.append(
        ("it waits for its window to close", answer["waits_for"] == "its window to close")
    )
    try:
        daemon.requeue_agent(daemon.HOST, {}, {"bundle": ops})
        found = None
    except daemon.Refused as refusal:
        found = str(refusal)
    checks.append(("it is queued once", found == f"{ops} is already queued"))
    (records / "1.json").write_text(json.dumps({**record, "status": "idle"}))
    deadline = time.monotonic() + 4
    while ops in windows and time.monotonic() < deadline:
        time.sleep(0.1)
    time.sleep(0.2)
    daemon.launch_queued()
    checks.append(("then for a slot", state["queue"][0]["waits_for"] == "a slot"))
    daemon.open_ops(daemon.HOST, ops, {"bundle": ops, "terminal": True})
    found = launched, state["queue"]
    checks.append(("a terminal opened on it drops its task", found == ([ops, ops], [])))
    for bundle, error in (
        (f"{daemon.OPS}{session[:-1]}0", f"409 {daemon.OPS}{session[:-1]}0: no conversation"),
        (f"{daemon.OPS}x", f"400 {daemon.OPS}SESSION: a session id"),
    ):
        found = run_client(path, "requeue", bundle)
        refused = found[0] == 1 and found[1][0].startswith(f"bctl: {error}")
        checks.append((f"requeue {bundle} is refused", refused))
    server.shutdown()
    server.server_close()
    daemon.is_agent_running, daemon.HOST_FOLDER = running, host_folder
    failed = [name for name, passed in checks if not passed]
    return [f"ops requeue: {name}: {state['queue']}" for name in failed], len(checks)


def check_place(root):
    """Real clients: place puts a queued task on a worker at once; an agent at work in its
    terminal, or busy again after its verdict, goes on there with its session once its container
    stopped; an idle one that gave its verdict closes, and its next task goes there; a remote
    agent comes back first, then goes on here or on the other worker before the tasks of its
    bundle, unless its work tree stayed there. Only the host asks, for an agent that runs."""
    stubs = {
        name: getattr(daemon, name)
        for name in (
            "REMOTE_AUTO",
            "WORKERS",
            "close_window",
            "get_session_status",
            "get_transcript",
            "remote_agent",
            "start_remote",
            "stop_after_turn",
        )
    }
    agents = {
        "waiting": FRESH,
        "busy": dict(RUNS_IT),
        "ended": {**RUNS_IT, "done": "0\n"},
        "answering": {**RUNS_IT, "done": "0\n"},
        "window": RUNS_ANOTHER_IN_WINDOW,
        "starting": {"remote-starting": ""},
        "launching": {**LAUNCHES_ANOTHER, "session": "s\n"},
        "unborn": {**RUNS_IT, "session": "unborn\n"},
        "verdict": {**RUNS_IT, "done": "0\n"},
        "paused": {"session": "s\n"},
        "away": {"session": "s\n", "remote.json": "{}"},
        "stuck": {"session": "s\n", "remote.json": "{}"},
    }
    state, launched = prepare(root, agents, 8)
    state["queue"] = [{**state["queue"][0], "where": "local"}]
    state["launches"]["launching"] = time.time()
    windows, stopping = {"busy", "ended", "answering", "unborn", "verdict"}, set()
    started, closed, moved = [], [], []
    remote = {"away": "sebpc", "stuck": "sebpc"}
    unreachable, asked = {}, []
    daemon.WORKERS = types.SimpleNamespace(
        names=["sebpc", "vm"],
        ask=asked.append,
        choose_agent=lambda wanted=None: wanted or "sebpc",
        get_agent_wait=unreachable.get,
        get_unreachable=unreachable.get,
        send_agent=lambda name, bundle: None,
    )

    def move_back(log, bundle, folder, workers):
        moved.append(bundle)
        remote.pop(bundle)
        return f"{bundle}: back"

    daemon.remote_agent = types.SimpleNamespace(
        is_remote=lambda folder: (
            folder.name in remote or (folder / ".agent" / "remote-starting").exists()
        ),
        move_back=move_back,
        read_remote=lambda folder: (
            {"worker": remote[folder.name]} if folder.name in remote else None
        ),
    )
    open_windows = daemon.get_open_windows
    daemon.get_open_windows = lambda: (
        open_windows() | {daemon.get_worktree_bundle_folder(bundle) for bundle in stopping}
    )
    daemon.get_session_status = lambda bundle, session: "busy" if bundle == "answering" else "idle"
    daemon.get_window_pids = lambda bundle: [1] * (bundle in windows)
    daemon.get_transcript = lambda session, *_: session not in (None, "unborn") and root / "t"
    daemon.close_window = windows.discard
    daemon.REMOTE_AUTO = False
    drops = {}
    daemon.stop_after_turn = lambda bundle, session, why, drop=False: (
        closed.append((bundle, why)) or drops.update({bundle: drop})
    )
    daemon.identify = lambda connection: daemon.HOST
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def start_remote(log, bundle, worker):
        (daemon.get_agent_folder(bundle) / "session").write_text("s\n")
        remote[bundle] = worker
        (daemon.get_agent_folder(bundle) / "remote-starting").unlink()
        started.append((bundle, worker))

    daemon.start_remote = start_remote

    def get_where(bundle):
        return (daemon.get_agent_folder(bundle) / "where").read_text()

    def get_agent_folder_where(bundle):
        return daemon.read_text(daemon.get_agent_folder(bundle) / "where")

    def close(bundle):
        windows.discard(bundle)
        del agents[bundle]["terminal"]
        (daemon.get_agent_folder(bundle) / "terminal").unlink()

    def get_queue():
        return [(item["bundle"], item["task"], item.get("waits_for")) for item in state["queue"]]

    checks = []
    try:
        found = run_client(path, "place", "waiting", "sebpc"), started, get_where("waiting")
        said = (0, ["waiting: its next task starts on sebpc"])
        expected = (said, [("waiting", "sebpc")], "worker:sebpc\n")
        checks.append(("a queued task placed here starts on the worker", found == expected))
        (daemon.get_agent_folder("stuck") / "where").write_text("local\n")
        found = run_client(path, "place", "stuck", "auto"), get_agent_folder_where("stuck")
        said = (0, ["stuck: its next task starts on a worker with a place, else here"])
        checks.append(("auto takes the place back, nothing moves", found == (said, None)))
        (daemon.get_agent_folder("stuck") / "where").unlink(missing_ok=True)
        code, lines = run_client(path, "place", "busy", "sebpc")
        item = state["queue"][-1]
        found = code, item["bundle"], item["resume"], item["task"], closed
        expected = 0, "busy", True, daemon.GO_ON, [("busy", "when Seb moved it to sebpc")]
        checks.append(("an agent at work is queued to go on after its turn", found == expected))
        wait = daemon.PAUSE_WAIT
        closes = f"busy: its window closes once its turn ends, in {wait} seconds at most"
        checks.append(("the client says so", lines[:1] == [closes] and "on sebpc" in lines[1]))
        refused = ["bctl: 409 the session of busy is queued already"]
        checks.append(
            ("a second move waits", run_client(path, "place", "busy", "vm") == (1, refused))
        )
        close("busy")
        daemon.launch_queued()
        found = started[-1], state["queue"]
        checks.append(("it goes on there once its window closed", found == (("busy", "sebpc"), [])))
        code, lines = run_client(path, "place", "ended", "vm")
        found = code, lines[1:], closed[-1], state["queue"], get_where("ended")
        expected = (
            0,
            ["ended: its next task starts on vm"],
            ("ended", "when Seb moved it to vm"),
            [],
            "worker:vm\n",
        )
        checks.append(("an idle agent that gave its verdict only closes", found == expected))
        code, _lines = run_client(path, "place", "answering", "vm")
        close("answering")
        stopping.add("answering")
        daemon.launch_queued()
        waits = get_queue()
        stopping.clear()
        daemon.launch_queued()
        found = code, waits, started[-1]
        expected = (
            0,
            [("answering", daemon.GO_ON, "its container here to stop")],
            ("answering", "vm"),
        )
        checks.append(
            ("one busy after its verdict goes on, once its container stopped", found == expected)
        )
        paused = {"bundle": "paused", "parent": "host", "queued": QUEUED, "task": daemon.GO_ON}
        state["queue"].append({**paused, "paused": True, "resume": True})
        code, lines = run_client(path, "place", "paused", "vm")
        found = code, get_where("paused"), [item["bundle"] for item in state["queue"]]
        checks.append(("a paused session takes the place", found == (0, "worker:vm\n", ["paused"])))
        state["queue"].clear()
        stubs["stop_after_turn"]("verdict", "s", "when Seb moved it", True)
        found = "verdict" in windows, state["queue"]
        checks.append(("a turn that ends on a verdict closes its window", found == (False, [])))
        state["queue"].append({**paused, "bundle": "verdict", "resume": True})
        windows.add("verdict")
        stubs["stop_after_turn"]("verdict", "s", "when Seb moved it", True)
        found = "verdict" in windows, state["queue"]
        checks.append(("and drops the session queued to go on", found == (False, [])))
        found = drops == {"busy": True, "ended": False, "answering": False}
        checks.append(("only a verdict given during the wait drops the session", found))
        refusals = (
            ("unborn", "vm", daemon.HOST, "409 the claude of unborn has not started yet"),
            ("ended", "local", daemon.HOST, "409 the agent of ended runs here already"),
            ("window", "sebpc", daemon.HOST, "409 window is open in a window"),
            ("away", "sebpc", daemon.HOST, "409 the agent of away runs on sebpc already"),
            ("starting", "local", daemon.HOST, "409 the agent of starting is starting on a worker"),
            ("launching", "vm", daemon.HOST, "409 the agent of launching is starting here"),
            (
                "waiting",
                "mars",
                daemon.HOST,
                "400 place: a bundle, and where: auto, local, sebpc, vm",
            ),
            ("waiting", "vm", "waiting", "409 place: only the host and the ops container"),
        )
        for bundle, where, caller, error in refusals:
            daemon.identify = lambda connection, caller=caller: caller
            code, lines = run_client(path, "place", bundle, where)
            refused = code == 1 and lines[0].startswith(f"bctl: {error}")
            checks.append((f"place {bundle} {where} from {caller} is refused", refused))
        daemon.identify = lambda connection: daemon.HOST
        unreachable["vm"] = ("drained", "vm is drained")
        found = run_client(path, "place", "waiting", "vm"), get_where("waiting"), list(asked)
        expected = (1, ["bctl: 409 vm is drained"]), "worker:sebpc\n", []
        checks.append(("a place on a worker that takes no agent is refused", found == expected))
        found = daemon.get_queue(daemon.HOST, {}, {})[1]["worker_states"], asked
        expected = {"vm": "drained"}, []
        checks.append(("the queue says why a worker takes no agent", found == expected))
        unreachable["vm"] = ("no link", "no connection to vm")
        found = run_client(path, "place", "waiting", "vm"), asked
        unreachable.clear()
        expected = (1, ["bctl: 409 no connection to vm"]), ["vm"]
        checks.append(("a place on a worker with no connection asks it", found == expected))
    finally:
        server.shutdown()
        server.server_close()
    logs = {}

    def start_job(caller, verb, work, lock=None, bundle=None):
        logs.setdefault(verb, []).append(io.StringIO())
        work(logs[verb][-1])
        return 202, {"job": "1"}

    daemon.start_job = start_job
    state["launches"].clear()
    try:
        found = daemon.get_queue(daemon.HOST, {}, {})[1]["remote"]
        checks.append(
            ("the queue names the remote agents", found == {"away": "sebpc", "stuck": "sebpc"})
        )
        state["queue"] = [{"bundle": "away", "parent": "host", "queued": QUEUED, "task": "new"}]
        started.clear()
        daemon.place_agent(daemon.HOST, {}, {"bundle": "away", "where": "vm"})
        found = moved, started, get_where("away"), get_queue()
        expected = (
            ["away"],
            [("away", "vm")],
            "worker:vm\n",
            [("away", "new", "the verdict of its agent on vm")],
        )
        checks.append(("a remote agent goes on on the other worker, first", found == expected))
        state["queue"].clear()
        remote["away"] = "vm"
        daemon.place_agent(daemon.HOST, {}, {"bundle": "away", "where": "local"})
        checks.append(("or here, in its terminal", launched[-1:] == ["away"]))
        (daemon.get_agent_folder("stuck") / "remote-back").mkdir()
        (daemon.get_agent_folder("stuck") / "where").write_text("local\n")
        started.clear()
        daemon.place_agent(daemon.HOST, {}, {"bundle": "stuck", "where": "vm"})
        found = logs["place"][-1].getvalue().splitlines()[-2:], started, launched[-1:]
        stays = [
            "stuck: its work tree did not come back, it stays local",
            "stuck: its session is queued to go on, same session",
        ]
        checks.append(
            ("a work tree left on its worker keeps it here", found == (stays, [], ["stuck"]))
        )
        tasks = [{"bundle": name, "parent": "host", "task": TASK} for name in agents]
        tasks[1]["where"] = "worker"
        found = [task["where"] for task in daemon.describe_queue({"queue": tasks})][:5]
        expected = ["worker:sebpc", "worker", "worker:vm", "worker:vm", "local"]
        checks.append(("the queue says where each task starts", found == expected))
    finally:
        vars(daemon).update(stubs, start_job=START_JOB, get_open_windows=open_windows)
    failed = [name for name, passed in checks if not passed]
    return [f"place: {name}: {get_queue()}" for name in failed], len(checks)


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
    victim = root / "victim"
    victim.write_text("kept\n")
    start.unlink()
    start.symlink_to(victim)
    os.utime(start, (0, 0), follow_symlinks=False)
    os.utime(victim, (0, 0))
    daemon.launch_queued()
    found = victim.read_text(), start.is_symlink()
    checks.append(("a link left in request/ is replaced, not followed", found == ("kept\n", False)))
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


def check_login(root):
    """A session the daemon queued again on the wait for the main login waits for a login that
    lives past RENEW_BEFORE, has bundle-ctl renew it meanwhile, and starts once it does, told so;
    a force of the queue leaves it, a force or a schedule of its bundle lets it go."""
    checks = []
    state, launched = prepare(root, {"expired": {"session": "s\n"}}, 8)
    stubs = {name: vars(daemon)[name] for name in ("get_window_pids", "stop_after_turn")}
    remote = {name: vars(daemon.remote_agent)[name] for name in ("get_login_left", "renew_login_soon")}
    left, renewed = [None], []
    vars(daemon).update(get_window_pids=lambda bundle: [1], stop_after_turn=lambda *args: None)
    vars(daemon.remote_agent).update(get_login_left=lambda: left[0], renew_login_soon=renewed.append)
    try:
        state["queue"].clear()
        daemon.requeue_agent(daemon.HOST, {}, {"bundle": "expired"}, login=True)
        daemon.force_queue(daemon.HOST, {}, {})
        found = launched, state["queue"][0].get("waits_for"), renewed[-1:]
        waits = ([], "the main login, which a laptop session or bundle-ctl renews")
        checks.append(("it waits, renewing it", found == (*waits, [root / "master" / "expired"])))
        left[0] = daemon.remote_agent.RENEW_BEFORE - 1
        daemon.launch_queued()
        checks.append(("a login renew_login renews is not enough", launched == []))
        left[0] = 3600
        daemon.launch_queued()
        task = (daemon.get_agent_folder("expired") / "task.md").read_text()
        back = task.startswith(daemon.GO_ON) and "The main login is renewed" in task
        checks.append(("it starts once renewed, told so", (launched, back) == (["expired"], True)))
        left[0] = None
        for name, action, body in (
            ("a force of its bundle", daemon.force_queue, {"bundle": "later"}),
            ("a schedule", daemon.schedule_task, {"bundle": "later", "when": "spot"}),
        ):
            state, launched = prepare(root / name.replace(" ", "-"), {"later": FRESH}, 8)
            state["queue"][0]["login"] = True
            action(daemon.HOST, {}, body)
            checks.append((f"{name} lets it go", launched == ["later"]))
    finally:
        vars(daemon).update(stubs)
        vars(daemon.remote_agent).update(remote)
    failed = [name for name, passed in checks if not passed]
    return [f"login: {name}: {state['queue']}" for name in failed], len(checks)


def check_disk_worker(root):
    """Under the disk floor, a task a worker takes goes there, and one not built yet waits; with
    the disk back, that one is built here, then its agent goes to the worker (Seb, 2026-11-02), or
    starts here in its bare folder when its build failed. With no GitHub connection, it waits for
    it rather than build (Seb, 2026-11-03: 21 builds failed so in a night, each agent started
    bare here for nothing)."""
    stubs = {
        name: getattr(daemon, name)
        for name in (
            "GITHUB_MUX",
            "REMOTE_AUTO",
            "WORKERS",
            "prepare_folder",
            "run_script",
            "start_remote",
        )
    }
    (root / "mux" / "sock").mkdir(parents=True)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(root / "mux" / "sock" / "github"))
    daemon.REMOTE_AUTO = True
    daemon.WORKERS = types.SimpleNamespace(
        names=["sebpc"],
        choose_agent=lambda wanted=None: "sebpc",
        send_agent=lambda name, bundle: None,
    )
    daemon.prepare_folder = lambda bundle: os.makedirs(
        daemon.get_worktree_bundle_folder(bundle), exist_ok=True
    )
    started, builds = [], []
    daemon.start_remote = lambda log, bundle, worker: started.append((bundle, worker))

    def fail(log, script, *args):
        raise daemon.Refused(f"{script} exited with 1")

    building = "new: its build here, then a worker"
    checks = []
    try:
        for disk, run_script, mux, expected in [
            (20.4, None, "mux", ([], [], ["new: disk space (20 GB left, 30 needed)"])),
            (100, lambda log, *args: None, "mux", ([("new", "sebpc")], [], [building])),
            (99, fail, "mux", ([], ["new"], [building])),
            (98, fail, "down", ([], [], ["new: the GitHub connection of the host"])),
        ]:
            started.clear()
            builds.clear()
            state, launched = prepare(root / str(disk), {"built": FRESH, "new": GONE}, 8, disk=disk)
            state["queue"][1]["build"] = ["fetch_bundle.py", "new", "--no-open"]
            daemon.GITHUB_MUX = root / mux
            daemon.run_script = run_script
            daemon.start_job = (
                lambda caller, verb, work, lock=None, bundle=None, run=daemon.start_job: (
                    builds.append(work)
                    if verb == "build"
                    else run(caller, verb, work, lock, bundle)
                )
            )
            daemon.launch_queued()
            waits = [f"{item['bundle']}: {item['waits_for']}" for item in state["queue"]]
            started.remove(("built", "sebpc"))
            with contextlib.redirect_stdout(io.StringIO()):
                for work in builds:
                    work(io.StringIO())
            found = list(started), launched, waits
            if builds:
                found += ([item["bundle"] for item in state["queue"] if item["bundle"] == "new"],)
                expected += ([],)
            checks.append((disk, found == expected, found))
        asked = (root / "down" / "request" / "start").exists()
        checks.append(("the GitHub connection is asked", asked, asked))
    finally:
        vars(daemon).update(stubs)
        listener.close()
    failed = [f"disk worker: {disk}: {found}" for disk, ok, found in checks if not ok]
    return failed, len(checks)


def check_bare_worker(root):
    """A session queued again from a bare folder, its build still to do, starts here, as a worker
    gets no repo from it, and a built bundle beside it goes to the worker (2026-11-03: six
    sessions that `bctl requeue --github` sent back went to sebpc, each refused with `agent:
    repos: an object of 1 to 8 repos`)."""
    stubs = {name: getattr(daemon, name) for name in ("REMOTE_AUTO", "WORKERS", "start_remote")}
    daemon.REMOTE_AUTO = True
    daemon.WORKERS = types.SimpleNamespace(
        names=["sebpc"],
        choose_agent=lambda wanted=None: "sebpc",
        send_agent=lambda name, bundle: None,
    )
    started = []
    daemon.start_remote = lambda log, bundle, worker: started.append(bundle)
    try:
        state, launched = prepare(root, {"built": FRESH, "bare": FRESH}, 8)
        state["builds"] = {"bare": ["fetch_bundle.py", "bare", "--no-open"]}
        state["queue"][1].update(resume=True, task=daemon.GO_ON)
        daemon.launch_queued()
    finally:
        vars(daemon).update(stubs)
    found = started, launched, state["queue"]
    return ([] if found == (["built"], ["bare"], []) else [f"bare worker: {found}"]), 1


def check_full_worker(root):
    """A task a worker would take, with no room here nor there, says why for both."""
    stubs = {name: getattr(daemon, name) for name in ("REMOTE_AUTO", "WORKERS")}
    daemon.REMOTE_AUTO = True
    daemon.WORKERS = types.SimpleNamespace(
        names=["sebpc"],
        choose_agent=lambda wanted=None: None,
        get_agent_wait=lambda name: ("full", "sebpc: the disk is short"),
    )
    try:
        state = prepare(root, {"fresh": FRESH, "here": FRESH}, 8, busy=True)[0]
        state["queue"][1]["where"] = "local"
        daemon.launch_queued()
    finally:
        vars(daemon).update(stubs)
    waits = [f"{item['bundle']}: {item['waits_for']}" for item in state["queue"]]
    expected = [
        "fresh: the CPU of the host, or a place for an agent: sebpc: the disk is short",
        "here: the CPU of the host",
    ]
    return ([] if waits == expected else [f"full worker: {waits}"]), 1


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
    stubs = {
        name: getattr(daemon, name)
        for name in ("get_agent", "get_open_bundle_folders", "get_repos", "has_write_tree")
    }
    daemon.get_agent = lambda bundle, head_date, alive: None
    daemon.get_open_bundle_folders = set
    daemon.get_repos = list
    daemon.has_write_tree = lambda: False
    state["builds"]["later"] = build
    try:
        rows = daemon.list_bundles()
    finally:
        vars(daemon).update(stubs)
        del state["builds"]["later"]
    checks.append(
        (
            "a build whose folder is gone lists no bundle",
            [(row["bundle"], row["folder"]) for row in rows] == [("new", True)],
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
    repo = root / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        + ["-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "work"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "branch", "new"], check=True)
    get_repos, get_repo_folder = daemon.get_repos, daemon.get_repo_folder
    daemon.get_repos = lambda: ["odoo"]
    daemon.get_repo_folder = lambda name: str(repo)
    state["builds"]["new"] = build
    try:
        daemon.build_bundle("new", {}, {})
        refusal = None
    except daemon.Refused as error:
        refusal = str(error)
    finally:
        daemon.get_repos, daemon.get_repo_folder = get_repos, get_repo_folder
    checks.append(
        (
            "a build again refuses to reset a commit no remote has",
            (refusal, ran) == ("new has unpushed commits in odoo, bctl build would reset them", [build]),
        ),
    )
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
    window = "ops-11111111-2222-3333-4444-555555555555"
    path = root / "create.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    daemon.identify = lambda connection: daemon.HOST
    check_fetch, daemon.outside.check_fetch = daemon.outside.check_fetch, lambda *args: None
    (root / "task.md").write_text(TASK)
    os.environ["AGENT_WINDOW"] = window
    try:
        sent = run_client(path, "create", "master", "y", "--task-file", str(root / "task.md"))
        daemon.fetch(daemon.HOST, {}, {"name": "master-z--seb", "task": TASK, "window": window})
    finally:
        del os.environ["AGENT_WINDOW"]
        daemon.outside.check_fetch = check_fetch
        server.shutdown()
        server.server_close()
    parents = {
        bundle: (item["parent"], daemon.read_parent(bundle).get("parent"))
        for item in state["queue"]
        if (bundle := item["bundle"]) in ("master-y--seb", "master-z--seb")
    }
    checks.append(
        (
            "a task bctl creates or fetches from an ops window has that window as parent",
            sent[0] == 0
            and parents == dict.fromkeys(("master-y--seb", "master-z--seb"), (window, window)),
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
    (bare / ".agent" / "task.md").write_text(TASK)
    for name, builds, expected in (
        ("failed", {"bare": build}, "opens"),
        ("never asked", {}, "refused"),
    ):
        state.update(builds=builds, queue=[])
        try:
            daemon.open_bundle(daemon.HOST, {}, {"bundle": "bare", "terminal": True})
            found = "opens"
        except daemon.Refused:
            found = "refused"
        checks.append((f"a terminal where a build {name}: {expected}", found == expected))
    return [f"build: {name}" for name, passed in checks if not passed], len(checks)


def check_gate_push(root):
    """A push git-gate sent at Seb's yes leaves the dev remote ref of the base repo on the head
    before it: the check fetches that branch before it refuses a fetch."""
    dev, repo = root / "dev.git", root / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(dev)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "fw", str(repo)], check=True)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    git += ["-c", "commit.gpgsign=false"]
    subprocess.run([*git, "remote", "add", "odoo-dev", str(dev)], check=True)

    def commit(message):
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", message], check=True)

    def check():
        try:
            CHECK_UNPUSHED("fw", "pfb")
        except daemon.Refused as error:
            return str(error)

    commit("first")
    subprocess.run([*git, "push", "-q", "odoo-dev", "fw"], check=True)
    commit("fix")
    subprocess.run([*git, "push", "-q", str(dev), "fw"], check=True)
    stubs = {
        name: vars(daemon)[name] for name in ("local_branches", "get_repo_folder", "session_env")
    }
    daemon.local_branches = lambda bundle: ["odoo"]
    daemon.get_repo_folder = lambda name: str(repo)
    daemon.session_env = lambda: dict(os.environ)
    try:
        sent = check()
        tracked = subprocess.run(
            [*git, "rev-parse", "refs/remotes/odoo-dev/fw", "fw"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout.split()
        commit("unpushed")
        unpushed = check()
        subprocess.run([*git, "push", "-q", str(dev), "--delete", "fw"], check=True)
        gone = check()
    finally:
        vars(daemon).update(stubs)
    refusal = "fw has unpushed commits in odoo, pfb would reset them"
    checks = [
        ("a head the gate sent passes", sent is None),
        ("its dev remote ref is fetched", tracked[0] == tracked[1]),
        ("a commit not pushed is refused", unpushed == refusal),
        ("a branch gone from the dev remote is refused", gone == refusal),
    ]
    return [f"gate push: {name}" for name, passed in checks if not passed], len(checks)


def check_build_cut(root):
    """A restart of the daemon kills the build a client follows: the client asks it again of the
    new daemon, which no longer knows the job."""
    state, _launched = prepare(root, {"new": FRESH}, 8)
    state["queue"] = []
    state["builds"] = {"new": ["fetch_bundle.py", "new", "--no-open"]}
    path = root / "ctl.sock"
    asked = []

    class Killed(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            asked.append("killed")
            data = b'{"job": "old"}'
            self.send_response(202)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.connection.close()
            threading.Thread(target=restart, daemon=True).start()

    def restart():
        killed.shutdown()
        killed.server_close()
        path.unlink()
        path.touch()
        time.sleep(4)
        path.unlink()
        new = daemon.Server(str(path), daemon.Handler)
        threading.Thread(target=new.serve_forever, daemon=True).start()

    killed = daemon.Server(str(path), Killed)
    threading.Thread(target=killed.serve_forever, daemon=True).start()
    daemon.STATE = root
    daemon.identify = lambda connection: "new"
    daemon.is_in_container = lambda connection: True
    daemon.check_unpushed = lambda bundle, script: None
    daemon.run_script = lambda log, *args: asked.append(args[0])
    daemon.start_job = START_JOB
    code, lines = run_client(path, "build")
    checks = [
        ("the client asks the build again", "bctl: a restart of bundle-ctl killed the job, asked again" in lines),
        ("the new daemon builds it", (code, asked) == (0, ["killed", "fetch_bundle.py"])),
        ("its build is done", state["builds"] == {}),
    ]
    return [f"build cut: {name}: {lines}" for name, passed in checks if not passed], len(checks)


def check_build_disk(root):
    """Real client: under the disk floor, `bctl build` of the agent of a bare bundle queues its
    session with its build, first of the tasks of its bundle, and its window closes once its turn
    ended; the task waits for disk space, then starts that session in the bare folder, told to
    build again. The host, an agent with no window, or a session queued already get the 409."""
    build = ["fetch_bundle.py", "bare", "--no-open"]
    bundles = {"bare": {**RUNS_IT, "parent": "ops\n", "priority": "5\n"}, "tab": RUNS_IT}
    state, launched = prepare(root, bundles, 8, disk=20.4)
    state["queue"] = [{"bundle": "bare", "parent": "ops", "queued": QUEUED + 60, "task": "next"}]
    state["builds"] = {"bare": build, "tab": build}
    windows = {"bare"}
    daemon.PAUSE_WAIT = 3
    daemon.check_unpushed = lambda bundle, script: None
    daemon.close_window = windows.discard
    daemon.get_session_status = lambda bundle, session: "idle"
    daemon.get_transcript = lambda session, *_: None
    daemon.get_window_pids = lambda bundle: [1] * (bundle in windows)
    daemon.identify = lambda connection: "bare"
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    code, lines = run_client(path, "build")
    said = (
        "20 GB left on the disk, under 30: not built. This session is queued 1 with its build, and"
        " its window closes once your turn ends, in 3 seconds at most: end your turn now. Once the"
        " disk is over 30 GB, the session goes on here by itself, told to run bctl build again."
    )
    checks = [("the client says to end the turn, exit 75", (code, lines) == (75, [said]))]
    item = state["queue"][0]
    found = (
        [queued["bundle"] for queued in state["queue"]],
        {key: item.get(key) for key in ("build", "parent", "priority", "resume", "session")},
        item["task"].startswith(daemon.GO_ON) and "run `bctl build` again" in item["task"],
    )
    expected = (
        ["bare", "bare"],
        {"build": build, "parent": "ops", "priority": 5, "resume": True, "session": "s"},
        True,
    )
    checks.append(("its session is queued with its build, first", found == expected))
    deadline = time.monotonic() + 6
    while windows and time.monotonic() < deadline:
        time.sleep(0.1)
    checks.append(("its window closes once its turn ended", not windows))
    windows.add("bare")
    refusals = []
    for caller, body in (("bare", {}), (daemon.HOST, {"bundle": "bare"}), ("tab", {})):
        try:
            daemon.build_bundle(caller, {}, body)
            refusals.append(None)
        except daemon.Refused as error:
            refusals.append(str(error).split(":")[0])
    windows.clear()
    refused = ["20 GB left on the disk, under 30"] * 3
    checks.append(("queued already, the host or no window: the 409", refusals == refused))
    daemon.get_open_windows = set
    daemon.launch_queued()
    waits = [queued["waits_for"].split(" (from")[0] for queued in state["queue"]]
    expected = [
        "disk space (20 GB left, 30 needed)",
        "the task queued before it for the bundle to start",
    ]
    checks.append(("it waits for disk space", (launched, waits) == ([], expected)))
    daemon.read_disk_free = lambda: 100
    daemon.prepare_folder = lambda bundle: None
    daemon.launch_queued()
    agent = daemon.get_agent_folder("bare")
    found = launched, daemon.read_text(agent / "session"), state["builds"]["bare"]
    checks.append(("with the space back, its session starts", found == (["bare"], "s", build)))
    task = agent.joinpath("task.md").read_text()
    told = task.startswith(f"{daemon.NOT_BUILT}\n\n{daemon.GO_ON}") and "`bctl build` again" in task
    checks.append(("told it is bare, to build again", told))
    server.shutdown()
    server.server_close()
    failed = [name for name, passed in checks if not passed]
    return [f"build disk: {name}: {state['queue']}" for name in failed], len(checks)


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
    waits = [SINCE.sub("T", f"{item['bundle']}: {item['waits_for']}") for item in state["queue"]]
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
                f"asking: {BUSY_SINCE}",
                f"typing: {IDLE}",
                f"dialog: {IDLE}",
                f"watching: {IDLE}",
                f"later: its time, {daemon.format_time(after)}",
                "other: the terminal of the previous agent of the bundle to close",
            ],
        ),
        ("hok gets no card", not any(daemon.HELD_DIR.glob("*.json"))),
    ]
    return [f"hand over: {name}" for name, passed in checks if not passed], len(checks)


def check_tell(root):
    """A task asked with --resume and a text of its own goes at once to the terminal that runs the
    session of its bundle, as a line of the host folder the terminal types, idle or busy, whatever
    the bundle left at .agent/pr-events: a session opened by hand gives no verdict (hok 519). Its
    verdict and its old task go to the history, its run.lock stays. Going on alone, a terminal the
    daemon closes, a task the terminal did not type yet and a bundle with no terminal wait as
    before."""
    by_hand = {
        **RUNS_ANOTHER,
        "task.md": "",
        "waiting": "",
        "container": "c\n",
        "model": "sonnet\n",
    }
    bundles = {
        "by-hand": by_hand,
        "ended": {**by_hand, "done": "0\n"},
        "go-on": by_hand,
        "closing": {**by_hand, "pause": ""},
        "untyped": STARTS_ANOTHER,
        "closed": by_hand,
        "linked": by_hand,
    }
    state, launched = prepare(root, bundles, 8)
    task = "fix it\nthen push\x1b[201~ it"
    for item in state["queue"]:
        item.update(resume=True, task=daemon.GO_ON if item["bundle"] == "go-on" else task)
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (daemon.get_agent_folder("linked") / "pr-events").symlink_to(elsewhere)
    terminals = daemon.get_terminals
    running = {*bundles, "bare"} - {"closed"}
    daemon.get_terminals = lambda: dict.fromkeys(running, 1)
    daemon.TOLD_DIR = root / "told"
    try:
        daemon.launch_queued()
        agent = daemon.get_agent_folder("by-hand")
        kept = sorted(path.name for path in agent.iterdir())
        history = sorted(path.name for path in next((agent / "history").iterdir()).iterdir())
        lines = {
            bundle: [path.read_text() for path in (root / "told" / bundle).glob("s/[!.]*.txt")]
            for bundle in bundles
        }
        waits = [
            SINCE.sub("T", f"{item['bundle']}: {item['waits_for']}") for item in state["queue"]
        ]
        bare = {"b0": FRESH, "bare": {**by_hand, "parent": "b0\n"}}
        prepare(root / "open", bare, 8)[0]["queue"].clear()
        opened = daemon.open_bundle("b0", {}, {"bundle": "bare", "resume": True, "task": "go"})
        bare_lines = [path.read_text() for path in (root / "told" / "bare").glob("s/[!.]*.txt")]
    finally:
        daemon.get_terminals = terminals
    told = "Task from host: fix it\nthen push [201~ it"
    checks = [
        ("no window opens", launched == []),
        (
            "the session by hand, the one that ended and the one with a link are told",
            lines["by-hand"] == lines["ended"] == lines["linked"] == [told],
        ),
        (
            "the others are not",
            not any(lines[name] for name in bundles if name not in ("by-hand", "ended", "linked")),
        ),
        ("nothing is written where a link leads", not any(elsewhere.iterdir())),
        (
            "its files and its run.lock stay",
            kept
            == [
                "container",
                "history",
                "model",
                "parent",
                "priority",
                "queued",
                "run.lock",
                "session",
                "task.md",
                "terminal",
            ],
        ),
        ("its old task and its wait go to the history", {"task.md", "waiting"} <= set(history)),
        ("task.md holds the task", daemon.read_text(agent / "task.md") == task.strip()),
        ("the model of the session stays", daemon.read_text(agent / "model") == "sonnet"),
        (
            "the others wait",
            waits
            == [f"{name}: {BUSY_SINCE}" for name in ("go-on", "closing")]
            + [f"untyped: {BUSY}"]
            + [f"closed: {BUSY_SINCE}"],
        ),
        (
            "bctl open --resume from its parent goes to it",
            opened[0] == 202 and bare_lines == ["Task from b0: go"],
        ),
    ]
    return [f"tell: {name}" for name, passed in checks if not passed], len(checks)


def check_remote_type(root):
    """A task for a bundle whose agent runs on a worker is typed in its session there, as its
    terminal here would: one asked with --resume and a text of its own at once, idle or busy, any
    other once that agent gave its verdict and is idle, then its Enter apart. Its verdict and its
    old task go to the history; its run.lock and the files of the remote agent stay. A dialog, a
    turn, a job that ends, a task for another session or for here, a link down and a stand-in that
    does not take the text keep it waiting, and a dialog opened after the text gets no Enter. The
    end of the turn it starts is written here, as the job writes its first verdict alone."""
    on_worker = {
        "model": "sonnet\n",
        "remote.json": "{}",
        "result.md": "pushed: x",
        "run.lock": "",
        "session": "s\n",
        "task.md": "old",
    }
    verdict = {**on_worker, "done": "0\n"}
    bundles = {
        "told": on_worker,
        "ended": verdict,
        "late": verdict,
        "working": on_worker,
        "answering": verdict,
        "dialog": verdict,
        "ending": {**verdict, "remote-ending": ""},
        "other": verdict,
        "here": verdict,
        "down": {**verdict, "remote-down": "10:00\n"},
        "refused": verdict,
    }
    state, launched = prepare(root, bundles, 8)
    task = "fix it\nthen push\x1b[201~ it"
    for item in state["queue"]:
        item["task"] = task
        item["resume"] = item["bundle"] in ("told", "dialog")
        if item["bundle"] == "other":
            item["session"] = "x"
        if item["bundle"] == "here":
            item["where"] = "local"
    statuses = {"told": "busy", "working": "busy", "answering": "busy", "dialog": "waiting"}
    records = {"stamp": 1000, "text": "Looking at it."}
    typed = []

    def type_keys(bundle, keys):
        if bundle == "refused":
            return "Connection refused"
        if bundle == "late":
            statuses["late"] = "waiting"
        typed.append((bundle, keys))
        return None

    names = (
        "REMOTE_ENTER_AFTER",
        "WORKERS",
        "get_last_text",
        "get_session_records",
        "get_session_status",
        "get_transcript",
        "remote_agent",
    )
    stubs = {name: getattr(daemon, name) for name in names}
    daemon.REMOTE_ENTER_AFTER = 0
    daemon.WORKERS = types.SimpleNamespace(
        names=[],
        is_leaving=lambda name: False,
        is_sending=lambda bundle: False,
        lists_agent=lambda name, bundle, after: None,
    )
    daemon.get_last_text = lambda transcript: records["text"]
    daemon.get_session_records = lambda container: {
        "s": {"status": statuses.get(container, "idle"), "statusUpdatedAt": records["stamp"]}
    }
    daemon.get_session_status = lambda bundle, session: statuses.get(bundle, "idle")
    daemon.get_transcript = lambda session, *_: root / "t"
    daemon.remote_agent = types.SimpleNamespace(
        is_remote=lambda folder: True,
        keep=lambda bundle, folder, workers: (None, False),
        read_remote=lambda folder: {"worker": "sebpc", "job": "1"},
        renew_login_soon=lambda folder: None,
        type_keys=type_keys,
    )
    agent = daemon.get_agent_folder("ended")
    journal = io.StringIO()
    try:
        with contextlib.redirect_stdout(journal):
            daemon.launch_queued()
            kept = sorted(path.name for path in agent.iterdir())
            history = sorted(path.name for path in next((agent / "history").iterdir()).iterdir())
            since = daemon.read_text(agent / "remote-typed")
            waits = [f"{item['bundle']}: {item['waits_for']}" for item in state["queue"]]
            ends, marks = [], ("done", "waiting", "remote-typed")
            for status, stamp, text in (
                ("busy", 2000, "Looking at it."),
                ("idle", 1000, "Looking at it."),
                ("idle", 2000, "Looking at it."),
                ("idle", 2000, "pushed: it is fixed"),
            ):
                statuses["ended"] = status
                records.update(stamp=stamp, text=text)
                daemon.keep_remote_agents()
                ends.append([name for name in marks if (agent / name).exists()])
    finally:
        vars(daemon).update(stubs)
    paste = f"{daemon.PASTE_START}fix it\nthen push [201~ it{daemon.PASTE_END}"
    told = f"{daemon.PASTE_START}Task from host: fix it\nthen push [201~ it{daemon.PASTE_END}"
    checks = [
        ("no window opens", launched == []),
        (
            "the resume is told at once, a task after the verdict typed, each with its Enter",
            typed
            == [("told", told), ("told", "\r"), ("ended", paste), ("ended", "\r"), ("late", paste)],
        ),
        (
            "its verdict and its old task go to the history",
            {"done", "result.md", "task.md"} <= set(history),
        ),
        (
            "its session, its run and the remote files stay",
            kept
            == [
                "history",
                "model",
                "parent",
                "priority",
                "queued",
                "remote-typed",
                "remote.json",
                "run.lock",
                "session",
                "task.md",
            ],
        ),
        ("task.md holds the task", daemon.read_text(agent / "task.md") == task.strip()),
        ("the status it was typed on is kept", since == "1000"),
        (
            "the others wait",
            waits
            == [
                "working: the verdict of its agent on sebpc",
                "answering: its agent on sebpc to be idle",
                "dialog: its agent on sebpc to be idle",
                "ending: the end of its agent on its worker",
                "other: the end of its agent on its worker",
                "here: the end of its agent on its worker",
                "down: the link to sebpc",
                "refused: the link to sebpc (Connection refused)",
            ],
        ),
        (
            "one that does not take the text keeps its verdict",
            (daemon.get_agent_folder("refused") / "done").exists(),
        ),
        (
            "a turn, a status of before, a text with no verdict, then the verdict",
            ends == [["remote-typed"], ["remote-typed"], ["waiting", "remote-typed"], ["done"]],
        ),
        ("its verdict is the last text", daemon.read_text(agent / "result.md") == records["text"]),
        (
            "the journal says so",
            [
                "told: the task from host is typed on sebpc",
                "ended: the task from host is typed on sebpc",
                "late: the task from host is typed on sebpc",
                "late: no Enter on its worker, a dialog opened after the text",
                "ended: its agent on its worker gave its verdict on the task typed there",
            ]
            == journal.getvalue().splitlines(),
        ),
    ]
    found = typed, waits, kept, ends, journal.getvalue()
    return [f"remote type: {name}: {found}" for name, passed in checks if not passed], len(checks)


def check_remote_told(root):
    """The lines the host leaves for a remote agent are typed in its session on its worker as one
    paste, then its Enter: from the folder of its session, then those of its earlier ones, then
    the PR events its stand-in left in its .agent/pr-events, idle, busy or on a background
    command. On a dialog, while its link is down or its job ends, they wait; a line that would
    type keys is dropped; a line in pr-events/ that prwatch.py does not write stays there for the
    hook of a session here; the lines the stand-in does not take are left again. After its
    verdict, its claude stops only once no line waits, its transcript rested FREE_AFTER and its
    last answer opens on a verdict."""
    session = "11111111-2222-3333-4444-555555555555"
    on_worker = {"remote.json": '{"worker": "sebpc", "job": "1"}', "session": f"{session}\n"}
    bundles = {
        "idle": on_worker,
        "busy": on_worker,
        "dialog": on_worker,
        "down": {**on_worker, "remote-down": "10:00\n"},
        "ending": {**on_worker, "remote-ending": ""},
        "refused": on_worker,
        "here": {"session": f"{session}\n"},
    }
    prepare(root, bundles, 8)
    daemon.TOLD_DIR = root / "told"
    for bundle in bundles:
        (daemon.TOLD_DIR / bundle).mkdir(parents=True)
        daemon.leave_event(daemon.TOLD_DIR / bundle, f"hok event: g1 of {bundle}", session)
    daemon.leave_event(daemon.TOLD_DIR / "idle", "PR event: odoo/odoo#1 from Firefox", "earlier")
    daemon.leave_event(daemon.TOLD_DIR / "busy", "\x1b[Z1", session)
    event = (
        "odoo#2 (https://github.com/odoo/odoo/pull/2), since 2026-11-02T10:00:00Z: 1 approval by"
        " aku-odoo; its head is now 0123456789ab, which this bundle does not have."
    )
    for bundle in ("idle", "dialog"):
        daemon.PRWATCH.leave(event, daemon.get_agent_folder(bundle) / "pr-events")
    daemon.PRWATCH.leave("Seb says go on.", daemon.get_agent_folder("idle") / "pr-events", "")
    told = f"PR event: {event} {daemon.PRWATCH.ADVICE}"
    statuses = {"busy": "busy", "dialog": "waiting"}
    texts = {"last": "Waiting for you: hok g12"}
    transcript = root / "t"
    transcript.write_text("")
    typed = []

    def type_keys(bundle, keys):
        if bundle == "refused":
            return "Connection refused"
        typed.append((bundle, keys))
        return None

    names = (
        "REMOTE_ENTER_AFTER",
        "TITLE",
        "get_last_text",
        "get_session_status",
        "get_transcript",
        "remote_agent",
    )
    stubs = {name: getattr(daemon, name) for name in names}
    daemon.REMOTE_ENTER_AFTER = 0
    daemon.TITLE = types.SimpleNamespace(is_resuming=lambda session, *_: False)
    daemon.get_last_text = lambda path: texts["last"]
    daemon.get_session_status = lambda bundle, session: statuses.get(bundle, "idle")
    daemon.get_transcript = lambda session, *_: texts.get("transcript", transcript)
    daemon.remote_agent = types.SimpleNamespace(
        read_remote=stubs["remote_agent"].read_remote,
        type_keys=type_keys,
    )
    journal = io.StringIO()
    try:
        with contextlib.redirect_stdout(journal):
            daemon.tell_remote_agents(daemon.get_remote_agents())
            daemon.tell_remote_agents(daemon.get_remote_agents())
        waiting = sorted(
            path.parent.parent.name for path in daemon.TOLD_DIR.glob(f"*/{session}/[!.]*.txt")
        )
        events = [daemon.PRWATCH.take(daemon.get_agent_folder(bundle) / "pr-events")
                  for bundle in ("idle", "dialog")]
        free = [daemon.is_remote_free(bundle, session) for bundle in ("busy", "dialog", "idle")]
        old = time.time() - daemon.FREE_AFTER - 1
        os.utime(transcript, (old, old))
        free.append(daemon.is_remote_free("idle", session))
        texts["last"] = "Pushed: it is fixed"
        free.append(daemon.is_remote_free("idle", session))
        texts["transcript"] = None
        free.append(daemon.is_remote_free("idle", session))
    finally:
        vars(daemon).update(stubs)
    paste = daemon.PASTE_START + "{}" + daemon.PASTE_END
    lines = journal.getvalue().splitlines()
    checks = [
        (
            "the idle and the busy one get their lines as one paste, then Enter, once",
            typed
            == [
                ("busy", paste.format("hok event: g1 of busy")),
                ("busy", "\r"),
                (
                    "idle",
                    paste.format(f"hok event: g1 of idle PR event: odoo/odoo#1 from Firefox {told}"),
                ),
                ("idle", "\r"),
            ],
        ),
        (
            "the others keep their line, a bundle here too",
            waiting == ["dialog", "down", "ending", "here", "refused"],
        ),
        ("a line that would type keys is dropped", any("would type keys" in line for line in lines)),
        (
            "a line in pr-events/ that prwatch.py does not write stays, a PR event on a dialog too",
            events == [["PR event: Seb says go on."], [told]],
        ),
        (
            "the journal says what was typed and what waits for the link",
            {
                "idle: 3 lines of the host typed on its worker",
                "busy: 1 line of the host typed on its worker",
                "refused: the lines of the host wait for the link: Connection refused",
            }
            <= set(lines),
        ),
        (
            "busy, a line waiting, a fresh transcript, no verdict: it stays; then it stops",
            free == [False, False, False, False, True, True],
        ),
    ]
    found = typed, waiting, free, lines
    return [f"remote told: {name}: {found}" for name, passed in checks if not passed], len(checks)


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


def check_renew(root):
    """The main login is renewed while a bundle container runs, with no remote agent."""
    prepare(root, {"open": {"session": "s\n", "task.md": TASK, "window": ""}}, 8)
    renewed = []
    renew = daemon.remote_agent.renew_login_soon
    daemon.remote_agent.renew_login_soon = renewed.append
    try:
        daemon.keep_remote_agents()
    finally:
        daemon.remote_agent.renew_login_soon = renew
    if renewed != [root / "master" / "open"]:
        return [f"renew: {renewed}"], 1
    return [], 1


def check_leaving(root):
    """The agents of a worker that answers `leaving` come back, once each, however many passes
    while their move runs: one at work, or busy again after its verdict, goes on here in a
    terminal and stays here; an idle one that gave its verdict stops there. One whose claude has
    not started yet, and the agents of a worker that stays, are left. A bundle placed on the
    worker with no agent there is placed here, once; one starting there, or placed on a worker
    that stays, is left."""
    on_sebpc, on_vm = {"where": "worker:sebpc\n"}, {"where": "worker:vm\n"}
    agents = {
        "answering": {**on_sebpc, "session": "s\n", "done": "0\n"},
        "elsewhere": on_vm,
        "placed": on_sebpc,
        "staying": {**on_vm, "session": "s\n"},
        "starting": {**on_sebpc, "remote-starting": ""},
        "unborn": {**on_sebpc, "session": "unborn\n"},
        "verdict": {**on_sebpc, "session": "s\n", "done": "0\n"},
        "working": {**on_sebpc, "session": "s\n"},
    }
    state, launched = prepare(root, agents, 8)
    state["queue"].clear()
    for bundle in ("answering", "staying", "unborn", "verdict", "working"):
        worker = "vm" if bundle == "staying" else "sebpc"
        remote = json.dumps({"worker": worker, "job": "1"})
        (daemon.get_agent_folder(bundle) / "remote.json").write_text(remote)
    jobs, moved, stops = [], [], []

    def move_back(log, bundle, folder, workers):
        moved.append(bundle)
        (folder / ".agent" / "remote.json").unlink()
        return f"{bundle}: back"

    names = ("WORKERS", "get_session_status", "get_transcript", "remote_agent", "start_job")
    stubs = {name: getattr(daemon, name) for name in names}
    module = stubs["remote_agent"]
    in_standin = module.in_standin
    module.in_standin = lambda bundle, verb, data=b"", check=True: stops.append(bundle)
    daemon.WORKERS = daemon.workers.Workers(["sebpc", "vm"])
    daemon.WORKERS.status = {
        "sebpc": (time.time(), {"draining": True, "leaving": True}),
        "vm": (time.time(), {}),
    }
    daemon.remote_agent = types.SimpleNamespace(
        end=module.end,
        is_remote=module.is_remote,
        keep=lambda bundle, folder, workers: (None, False),
        move_back=move_back,
        read_remote=module.read_remote,
        renew_login_soon=lambda folder: None,
    )
    daemon.get_session_status = lambda bundle, session: "busy" if bundle == "answering" else "idle"
    daemon.get_transcript = lambda session, *_: session != "unborn" and root / "t"
    daemon.start_job = lambda caller, verb, work, lock=None, bundle=None: jobs.append(
        (bundle, work)
    )
    journal = io.StringIO()
    try:
        with contextlib.redirect_stdout(journal):
            daemon.keep_remote_agents()
            daemon.keep_remote_agents()
            started = [bundle for bundle, _work in jobs]
            for _bundle, work in jobs:
                work(io.StringIO())
            daemon.keep_remote_agents()
    finally:
        vars(daemon).update(stubs)
        module.in_standin = in_standin
    wheres = [
        daemon.read_text(daemon.get_agent_folder(bundle) / "where") for bundle in (*moved, *stops)
    ]
    places = {
        bundle: daemon.read_text(daemon.get_agent_folder(bundle) / "where")
        for bundle in ("elsewhere", "placed", "staying", "starting", "unborn")
    }
    checks = [
        ("one move each, while it runs", started == ["answering", "working"]),
        ("they go on here in a terminal", moved == launched == ["answering", "working"]),
        ("their next tasks run here", wheres == ["local", "local", "local"]),
        ("the idle one stops there, once", stops == ["verdict"]),
        (
            "a bundle placed there with no agent there is placed here",
            places
            == {
                "elsewhere": "worker:vm",
                "placed": "local",
                "staying": "worker:vm",
                "starting": "worker:sebpc",
                "unborn": "worker:sebpc",
            },
        ),
        (
            "the journal says why",
            journal.getvalue().splitlines()
            == [
                "answering: back from sebpc, the worker is leaving",
                "verdict: its agent stops on sebpc, the worker is leaving",
                "working: back from sebpc, the worker is leaving",
                "placed: placed here, sebpc is leaving",
            ],
        ),
    ]
    failed = [name for name, passed in checks if not passed]
    found = started, moved, launched, stops, places, journal.getvalue()
    return [f"leaving: {name}: {found}" for name in failed], len(checks)


def check_orphans(root):
    """An agent a worker lists past REMOTE_GONE_AFTER that no bundle here has gets one card: its
    folder is gone, or it names no agent on that worker. One known, starting or on another
    worker gets none, nor one whose job nothing here names, which the journal says. The yes
    cancels its job there and drops what it left here (2026-11-02: an agent ran a day on sebpc
    after the deletion of its bundle)."""
    on = {name: json.dumps({"worker": name, "job": "1"}) for name in ("sebpc", "vm")}
    bundles = {
        "elsewhere": {"remote.json": on["vm"]},
        "gone": None,
        "known": {"remote.json": on["sebpc"]},
        "recreated": {"session": "s\n"},
        "starting": {"remote-starting": ""},
    }
    prepare(root, bundles, 8)
    records = {
        "elsewhere": {"worker": "vm", "job": "3"},
        "gone": {"worker": "sebpc", "job": "7", "dummy": "a" * 64},
        "recreated": {"worker": "sebpc", "job": "8"},
    }
    module = daemon.remote_agent
    stubs = {name: getattr(module, name) for name in ("STATE", "drop_orphan", "ssh")}
    module.STATE = root / "state"
    for bundle, record in records.items():
        module.keep_record(bundle, record)
    (root / "projects" / "-workspace").mkdir(parents=True)
    (root / "projects" / "-workspace" / "s1.jsonl").write_text('{"customTitle": "gone"}\n')
    calls, drops = [], []
    module.ssh = lambda workers, worker, verb, bundle, *args, **kwargs: (
        calls.append((worker, verb, bundle, *args)) or {"cancelled": args[0]}
    )
    module.drop_orphan = lambda bundle, record: drops.append((bundle, record))
    projects, daemon.PROJECTS = daemon.PROJECTS, root / "projects"
    workers_stub, daemon.WORKERS = daemon.WORKERS, daemon.workers.Workers(["sebpc", "vm"])
    listed = {
        "sebpc": ["known", "starting", "gone", "recreated", "elsewhere"],
        "vm": ["elsewhere"],
    }
    daemon.WORKERS.status = {
        name: (
            time.time(),
            {"every": {"agents": [{"bundle": b, "kind": "agent", "age": 600} for b in agents]}},
        )
        for name, agents in listed.items()
    }
    journal = io.StringIO()
    try:
        with contextlib.redirect_stdout(journal):
            daemon.ask_orphans()
            first = journal.getvalue()
            for key in daemon.orphans:
                daemon.orphans[key] -= daemon.REMOTE_GONE_AFTER + 1
            daemon.ask_orphans()
            daemon.ask_orphans()
            path = daemon.HELD_DIR / "1.json"
            item = daemon.HELD.read_json(path)
            other = daemon.HELD.read_json(daemon.HELD_DIR / "2.json")
            path.with_suffix(".answer").write_text(f"yes {item['digest']}\n")
            daemon.HELD.take(path.with_suffix(".answer"), daemon.send_held)
            result = daemon.HELD.read_json(path.with_suffix(".result"))
    finally:
        vars(module).update(stubs)
        daemon.PROJECTS, daemon.WORKERS = projects, workers_stub
        daemon.orphans.clear()
    checks = [
        ("nothing before REMOTE_GONE_AFTER", first == ""),
        (
            "one card each for the gone and the recreated bundle, a line for the one with no job",
            [line for line in journal.getvalue().splitlines() if not line.startswith("{")]
            == [
                "gone: its agent runs on sebpc with no bundle here: hok b1",
                "recreated: its agent runs on sebpc with no bundle here: hok b2",
                "stale wait: elsewhere: its agent runs on sebpc, and nothing here names its job",
                "gone: its agent on sebpc is cancelled, no bundle here had it",
            ],
        ),
        (
            "the card names the job, why, and the newest session",
            (item["title"], item["lines"][1], item["lines"][2].split(",")[0], other["lines"][1:3])
            == (
                "cancel the agent of gone on sebpc",
                "its bundle has no folder here",
                "its newest session here: s1",
                ["the bundle here has no agent on sebpc", "no session of it here"],
            ),
        ),
        (
            "the yes cancels its job and drops what it left here",
            (result["text"], calls, drops)
            == (
                "the agent of gone on sebpc is cancelled (job 7), its tree stays there",
                [("sebpc", "cancel", "gone", "7")],
                [("gone", records["gone"])],
            ),
        ),
    ]
    failed = [name for name, passed in checks if not passed]
    found = journal.getvalue(), item, other, result, calls, drops
    return [f"orphans: {name}: {found}" for name in failed], len(checks)


def check_choose_agent(root):
    """A worker takes an agent only with a place and 1 GB free for it, as it refuses one
    without (2026-10-30: six agents sent to sebpc in three minutes, each refused, then here), and
    a task placed there says which is missing (Seb, 2026-11-02: "why isn't there a clear way to
    tell why there is no place?")."""
    found = daemon.workers.Workers(["sebpc"])
    found.is_up = lambda name: True
    failures = []
    taken = "full", "every agent place of sebpc is taken"
    for status, expected, why in (
        (
            {"agents_free": 1, "gigabytes_free": 0.8},
            None,
            ("full", "sebpc has 0.8 GB free, 1 needed"),
        ),
        ({"agents_free": 0, "gigabytes_free": 5}, None, taken),
        ({"agents_free": 1, "gigabytes_free": 1.0}, "sebpc", None),
        (
            {"agents_free": 0, "gigabytes_free": 5, "agent_wait": "the disk is short"},
            None,
            ("full", "sebpc: the disk is short"),
        ),
        ({"agents_free": 1}, None, ("full", "sebpc has 0.0 GB free, 1 needed")),
        (
            {"agents_free": 1, "gigabytes_free": 5, "draining": True},
            None,
            ("drained", "sebpc is drained"),
        ),
    ):
        found.status["sebpc"] = (time.time(), status)
        for wanted in (None, "sebpc"):
            if found.choose_agent(wanted) != expected:
                failures.append(f"choose_agent {status} {wanted}: expected {expected}")
        if found.get_agent_wait("sebpc") != why:
            failures.append(f"get_agent_wait {status}: {found.get_agent_wait('sebpc')}")
    sending = daemon.workers.Workers(["sebpc"])
    sending.is_up = lambda name: True
    seen = time.time()
    sending.status["sebpc"] = (seen, {"agents_free": 2, "gigabytes_free": 5})
    sending.send_agent("sebpc", "first")
    waits = [sending.get_agent_wait("sebpc")]
    sending.send_agent("sebpc", "second")
    waits.append(sending.get_agent_wait("sebpc"))
    sending.end_send("sebpc", "first")
    sending.end_send("sebpc", "second")
    waits.append(sending.get_agent_wait("sebpc"))
    sending.status["sebpc"] = (seen + 60, {"agents_free": 2, "gigabytes_free": 1.5})
    waits.append(sending.get_agent_wait("sebpc"))
    sending.send_agent("sebpc", "third")
    waits.append(sending.get_agent_wait("sebpc"))
    if waits != [None, taken, taken, None, ("full", "sebpc has 0.5 GB free, 1 needed")]:
        failures.append(f"agents sent before the next status: {waits}")
    mux, workers = daemon.workers.MUX, daemon.WORKERS
    daemon.workers.MUX, daemon.WORKERS = root, found
    try:
        item = {"bundle": "placed", "where": "worker:sebpc"}
        wait = daemon.get_agent_worker(item, root, False)
        if wait != (None, "a place for an agent: sebpc is drained"):
            failures.append(f"the wait of a placed task: {wait}")
        found.status["sebpc"] = (0, {})
        stale = found.get_unreachable("sebpc")
        found.is_up = lambda name: False
        down = found.get_unreachable("sebpc"), found.get_agent_wait("sebpc")
        if (root / "sebpc" / "request").exists():
            failures.append("a read of a worker with no connection asks it")
        found.choose_agent()
        (root / "sebpc" / "request" / "held").touch()
        held = found.get_unreachable("sebpc")
    finally:
        daemon.workers.MUX, daemon.WORKERS = mux, workers
    held_why = "no connection to sebpc, its key is asked at the next unlock of the screen"
    for name, why, expected in (
        ("stale", stale, ("no answer", "no answer from sebpc yet")),
        ("down", down, (("no link", "no connection to sebpc"),) * 2),
        ("held", held, ("no link", held_why)),
    ):
        if why != expected:
            failures.append(f"get_unreachable {name}: {why}")
    return failures, 24


def check_fallback(root):
    """An agent its worker refuses starts here this once, and the next tasks of its bundle are
    `auto` again (2026-10-30: one refusal each kept 9 bundles here for good)."""
    stubs = {
        name: getattr(daemon, name)
        for name in ("REMOTE_AUTO", "WORKERS", "launch_queued", "remote_agent")
    }
    state, _launched = prepare(root, {"refused": {"parent": "host\n"}}, 8)
    daemon.launch_queued = lambda: None
    state["queue"].clear()
    ended = []

    def start(*args):
        raise RuntimeError("no memory left for an agent")

    daemon.REMOTE_AUTO = True
    daemon.WORKERS = types.SimpleNamespace(
        names=["sebpc"], end_send=lambda name, bundle: ended.append((name, bundle))
    )
    daemon.remote_agent = types.SimpleNamespace(start=start)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            daemon.start_remote(io.StringIO(), "refused", "sebpc")
        item = state["queue"][0]
        folder = daemon.get_agent_folder("refused")
        here = daemon.get_task_where(item, folder)
        daemon.write_task(item)
        found = (
            here,
            (folder / "where").exists(),
            daemon.get_task_where({"bundle": "refused", "task": TASK}, folder),
            ended,
        )
    finally:
        vars(daemon).update(stubs)
    expected = ("local", False, "auto", [("sebpc", "refused")])
    return ([] if found == expected else [f"fallback: {found}"]), 1


def check_refused_once(root):
    """A task its worker refuses starts here once, with its priority and effort, and never goes
    to the worker again, whatever pass comes before its job takes the lock of the bundle or
    before its requeue takes the queue (2026-11-02: four such tasks started on sebpc ten minutes
    later, and their copy waited to run them a second time here)."""
    stubs = {name: getattr(daemon, name) for name in ("REMOTE_AUTO", "WORKERS", "enqueue")}
    start = daemon.remote_agent.start
    state, launched = prepare(root, {"refused": FRESH}, 8)
    state["queue"][0].update(priority=-15, effort="high")
    sent = []

    def refuse(log, bundle, folder, *args):
        (folder / ".agent" / "remote-starting").unlink()
        raise RuntimeError("the disk is short")

    def enqueue(*args, **kwargs):
        # Simulate the pass of a container event that takes the queue first.
        daemon.launch_queued()
        return stubs["enqueue"](*args, **kwargs)

    daemon.REMOTE_AUTO = True
    daemon.WORKERS = types.SimpleNamespace(
        names=["sebpc"],
        choose_agent=lambda wanted=None: "sebpc",
        send_agent=lambda name, bundle: None,
        end_send=lambda name, bundle: None,
    )
    daemon.enqueue = enqueue
    daemon.remote_agent.start = refuse
    daemon.start_job = lambda caller, verb, work, lock=None, bundle=None: (
        sent.append(work) if verb == "remote-agent" else work(None)
    )
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            daemon.launch_queued()
            daemon.launch_queued()
            sent[0](io.StringIO())
    finally:
        vars(daemon).update(stubs)
        daemon.remote_agent.start = start
    agent = daemon.get_agent_folder("refused")
    found = (
        len(sent),
        launched,
        [item["bundle"] for item in state["queue"]],
        daemon.read_text(agent / "priority"),
        daemon.read_text(agent / "effort"),
    )
    expected = (1, ["refused"], [], "-15", "high")
    return ([] if found == expected else [f"refused once: {found}"]), 1


def check_stuck(root):
    """A task waits since the pass that gave its reason a new kind, and shows stuck in GET /queue
    and `bctl queue` once it waited past the limit of that kind: a reason of the same kind keeps
    its time, a pause is never stuck (2026-11-02: 8 tasks waited an hour on the agents of sebpc,
    with no age shown)."""
    state, launched = prepare(root, {"busy": RUNS_ANOTHER, "fresh": FRESH, "paused": FRESH}, 1)
    state["queue"][2]["paused"] = True
    daemon.identify = lambda connection: daemon.HOST
    daemon.is_in_container = lambda connection: False
    daemon.launch_queued()
    old = time.time() - 4 * 3600 - 1
    for item in state["queue"]:
        item["waits_since"] = old
    daemon.launch_queued()
    stuck = [item["stuck"] for item in daemon.describe_queue(state)]
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _code, lines = run_client(path, "queue")
    server.shutdown()
    server.server_close()
    memory = daemon.describe_memory
    daemon.MAX_AGENTS = 8
    daemon.describe_memory = lambda open_windows: {**memory(open_windows), "short": True}
    daemon.launch_queued()
    after = [(item["waits_for"], item["stuck"]) for item in daemon.describe_queue(state)][1:]
    since = f"from host, since {daemon.format_time(QUEUED)}, stuck since {daemon.format_time(old)}"
    checks = [
        ("past the limit but a pause", stuck == [daemon.format_time(old)] * 2 + [None]),
        (
            "bctl queue says so",
            lines[2:3] == [f"queued 2: fresh (priority 0, {since}, waits for a slot)"],
        ),
        (
            "a new kind starts again",
            after == [("memory on the host", None), ("a manual resume", None)],
        ),
        ("nothing starts", launched == []),
    ]
    found = stuck, lines, after
    return [f"stuck: {name}: {found}" for name, passed in checks if not passed], len(checks)


def check_stale(root):
    """What a wait names is checked each pass: an agent its worker no longer lists, in answers
    asked after its start, ends as lost after REMOTE_GONE_AFTER, and a start no job runs is
    dropped with its stand-in, its task queued here when it gave no verdict, each with one `stale
    wait` line (2026-11-03: two such tasks of a restart vanished, then their bundle as unused). A
    listed agent, a worker with no fresh answer, a start that runs or just began are left. A
    terminal killed before its run holds no start."""
    agents = {
        "alive": {"session": "s\n", "task.md": TASK},
        "gone": {"session": "s\n", "task.md": TASK},
        "silent": {"session": "s\n", "task.md": TASK},
        "ended": {"remote-starting": "", "task.md": TASK, "done": "0\n"},
        "late": {"remote-starting": "", "task.md": TASK},
        "orphan": {"remote-starting": "", "task.md": TASK, "effort": "high\n"},
        "sending": {"remote-starting": ""},
        "new": {"remote-starting": ""},
    }
    state, _launched = prepare(root, agents, 8)
    state["queue"].clear()
    started = time.time() - 600
    for bundle in ("alive", "gone", "silent"):
        remote = {"worker": "vm" if bundle == "silent" else "sebpc", "job": "1", "started": started}
        (daemon.get_agent_folder(bundle) / "remote.json").write_text(json.dumps(remote))
    for bundle in ("ended", "late", "orphan", "sending"):
        when = started - daemon.remote_agent.STARTING if bundle == "late" else started
        os.utime(daemon.get_agent_folder(bundle) / "remote-starting", (when, when))
    finished, enqueued, dropped = [], [], []

    def finish(bundle, folder, end):
        finished.append(bundle)
        (folder / ".agent" / "remote.json").unlink()
        return f"{bundle}: its agent on sebpc ended ({end['reason']})", True

    names = ("WORKERS", "enqueue", "get_transcript", "remote_agent")
    stubs = {name: getattr(daemon, name) for name in names}
    module = stubs["remote_agent"]
    daemon.WORKERS = daemon.workers.Workers(["sebpc", "vm"])
    agent = {"bundle": "alive", "kind": "agent", "age": 600}
    daemon.WORKERS.status = {
        "sebpc": (time.time(), {"every": {"agents": [agent]}}),
        "vm": (time.time() - 3600, {}),
    }
    daemon.WORKERS.sent = {"sebpc": {"sending": float("inf")}}
    daemon.enqueue = lambda caller, bundle, body: enqueued.append((bundle, body))
    daemon.get_transcript = lambda session, *_: None
    daemon.remote_agent = types.SimpleNamespace(
        STARTING=module.STARTING,
        drop_start=dropped.append,
        finish=finish,
        keep=lambda bundle, folder, workers: (None, False),
        read_remote=module.read_remote,
        renew_login_soon=lambda folder: None,
    )
    journal = io.StringIO()
    try:
        with contextlib.redirect_stdout(journal):
            daemon.keep_remote_agents()
            first = list(finished)
            for bundle, since in daemon.missing_agents.items():
                daemon.missing_agents[bundle] = since - daemon.REMOTE_GONE_AFTER
            daemon.keep_remote_agents()
            daemon.keep_remote_agents()
    finally:
        vars(daemon).update(stubs)
        daemon.missing_agents.clear()
    starting = [
        bundle
        for bundle in ("ended", "late", "orphan", "sending", "new")
        if (daemon.get_agent_folder(bundle) / "remote-starting").exists()
    ]
    here = ("task", "where", "once", "effort", "resume")
    requeued = [(bundle, [body.get(key) for key in here]) for bundle, body in enqueued[:-1]]
    state, _launched = prepare(
        root / "start", {"dead": {**STARTS_ANOTHER, "dead": ""}, "fresh": FRESH}, 8, True
    )
    with contextlib.redirect_stdout(journal):
        daemon.launch_queued()
        daemon.launch_queued()
    waits = [f"{item['bundle']}: {item['waits_for']}" for item in state["queue"]]
    checks = [
        ("an agent missing once is left", first == []),
        ("the agent gone ends as lost, once", finished == ["gone"]),
        ("its task goes on", enqueued[-1:] == [("gone", {"task": TASK})]),
        ("a start that runs or just began stays", starting == ["sending", "new"]),
        (
            "a dead start drops its stand-in, past 30 minutes too",
            dropped == ["ended", "late", "orphan"],
        ),
        (
            "its task is queued here once, with its effort, not after a verdict",
            requeued
            == [
                ("late", [TASK, "local", True, None, False]),
                ("orphan", [TASK, "local", True, "high", False]),
            ],
        ),
        (
            "one line each",
            journal.getvalue().splitlines()
            == [
                "stale wait: ended: no start of its agent runs on a worker",
                "stale wait: late: no start of its agent runs on a worker, its task is queued here",
                "stale wait: orphan: no start of its agent runs on a worker, its task is queued here",
                "stale wait: gone: its agent on sebpc ended (gone from its worker)",
                "stale wait: dead: its terminal died before its run",
            ],
        ),
        ("no task waits for the dead start", "fresh: memory on the host" in waits),
    ]
    found = first, finished, enqueued, dropped, starting, journal.getvalue(), waits
    return [f"stale: {name}: {found}" for name, passed in checks if not passed], len(checks)


def main():
    failures = []
    starts = {start for start, _limit in daemon.WAIT_LIMITS}
    unnamed = set()
    classify = daemon.get_wait_limit

    def get_wait_limit(waits_for):
        kind, limit = classify(waits_for)
        if waits_for and kind not in starts:
            unnamed.add(waits_for)
        return kind, limit

    daemon.get_wait_limit = get_wait_limit
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
        check_stuck,
        check_stale,
        check_moves,
        check_disk,
        check_after,
        check_pause,
        check_requeue,
        check_ops_requeue,
        check_place,
        check_github,
        check_login,
        check_build,
        check_gate_push,
        check_disk_worker,
        check_bare_worker,
        check_full_worker,
        check_build_cut,
        check_build_disk,
        check_parked,
        check_priority,
        check_links,
        check_stranded_link,
        check_outside,
        check_label,
        check_second_task,
        check_hand_over,
        check_tell,
        check_remote_type,
        check_remote_told,
        check_ramp,
        check_renew,
        check_leaving,
        check_orphans,
        check_choose_agent,
        check_fallback,
        check_refused_once,
    ):
        with tempfile.TemporaryDirectory() as root:
            failed, count = check(pathlib.Path(root))
        failures += failed
        total += count
    failures += [f"a reason WAIT_LIMITS does not name: {reason}" for reason in sorted(unnamed)]
    total += 1
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
