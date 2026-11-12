#!/usr/bin/env python3
"""What open --terminal refuses, and what open ops opens, without podman: python3 test_open.py"""

import contextlib
import io
import json
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

ONLY_OWN = "bundle: only the host, the bundle or its parent opens its terminal"
# The files of the .agent folder, whether its container has a window, the caller and the refusal.
# "parent" queued the last task of the bundle; "other" is any other bundle (B11).
CASES = [
    ({}, False, "parent", "bundle has no agent session to open in a terminal"),
    ({}, False, "bundle", "bundle has no agent session to open in a terminal"),
    ({}, False, daemon.HOST, None),
    ({"task.md"}, False, "parent", "bundle has no agent session to open in a terminal"),
    ({"task.md"}, False, daemon.HOST, "bundle has no agent session to open in a terminal"),
    ({"session"}, False, daemon.HOST, "bundle has no agent session to open in a terminal"),
    ({"task.md", "session", "held"}, False, "parent", "a terminal holds its session"),
    ({"task.md", "session", "held"}, True, "bundle", "a terminal holds its session"),
    ({"task.md", "session"}, True, "parent", "bundle is open in a window"),
    ({"task.md", "session"}, True, "bundle", None),
    ({"task.md", "session"}, True, daemon.HOST, None),
    ({"task.md", "session"}, False, "parent", None),
    ({"task.md", "session"}, False, "other", ONLY_OWN),
    ({"task.md", "session"}, True, "other", ONLY_OWN),
    ({}, False, "other", ONLY_OWN),
]
BODIES = [
    ({"terminal": True, "task": "do"}, "terminal: only without a task"),
    ({"terminal": True, "resume": True}, "terminal: only without a task"),
]
HOLD = "hold-ops: a task alone"
MOVED = "ops-0f897331-3dea-4b5b-a0c4-1c27061e4efc"
NEW = "ops-11111111-2222-3333-4444-555555555555"
ONLY_HOST = "ops: only the host and the ops container open it, a bundle sends a task"
ONLY_TERMINAL = "ops-SESSION: a session id, alone or with terminal"
RUNNING = "ops-71d0d27c-e3f6-46e7-96dc-f7b640411a66"
# The caller, the body of open ops, and what opens or the refusal. ops-SESSION is the session of a
# tab moved to a window, or alone of a window moved to a tab: MOVED and RUNNING have a transcript,
# and an agent-terminal.py runs RUNNING.
OPS_CASES = [
    ("bundle", {"terminal": True}, ONLY_HOST),
    ("bundle", {"task": "do"}, "held b1"),
    ("bundle", {"task": "do"}, "held b1"),
    ("bundle", {"task": "other"}, "held b2"),
    ("bundle", {"task": "do", "model": "opus"}, ONLY_HOST),
    ("bundle", {"task": " "}, "task: a text of at most 32768 characters"),
    (daemon.HOST, {"task": "do", "priority": 1}, "ops: only a task or terminal"),
    (daemon.HOST, {"task": "do", "hold": True}, "ops: only a task or terminal"),
    (daemon.HOST, {"task": " "}, "task: a text of at most 32768 characters"),
    (daemon.HOST, {}, "window ops"),
    (daemon.HOST, {"terminal": True}, "terminal ops- with no task"),
    (daemon.HOST, {"task": "do"}, "terminal ops- on do"),
    (daemon.HOST, {"bundle": MOVED, "terminal": True}, f"terminal {MOVED} with no task"),
    ("bundle", {"bundle": MOVED, "terminal": True}, ONLY_HOST),
    (daemon.HOST, {"bundle": MOVED}, f"tab {MOVED} window ops"),
    (daemon.HOST, {"bundle": RUNNING}, f"tab {RUNNING} window ops"),
    ("bundle", {"bundle": RUNNING}, ONLY_HOST),
    (daemon.HOST, {"bundle": NEW}, f"{NEW}: no conversation to go on with"),
    (daemon.HOST, {"bundle": MOVED, "task": "do"}, ONLY_TERMINAL),
    (daemon.HOST, {"bundle": MOVED, "task": "do", "terminal": True}, ONLY_TERMINAL),
    (daemon.HOST, {"bundle": "ops-x", "terminal": True}, ONLY_TERMINAL),
    (daemon.HOST, {"bundle": NEW, "terminal": True}, f"{NEW}: no conversation to go on with"),
    (daemon.HOST, {"bundle": RUNNING, "terminal": True}, f"{RUNNING}: already in a window"),
]
# The caller of a task on the bundle, the bundle that queued its last task, the body, and what
# becomes of the task: queued with no yes, or held for Seb's.
TASK_CASES = [
    (daemon.HOST, None, {"task": "do"}, "queued by host"),
    ("bundle", None, {"task": "do"}, "queued by bundle"),
    ("parent", "parent", {"task": "do"}, "queued by parent"),
    ("other", "parent", {"task": "do"}, "held b3"),
    ("other", None, {"task": "do"}, "held b3"),
    ("other", "parent", {"task": daemon.GO_ON, "resume": True}, "held b4"),
    ("parent", "parent", {"task": daemon.GO_ON, "resume": True}, "queued by parent"),
    (daemon.HOST, None, {"task": "do", "window": NEW}, f"queued by {NEW}"),
    (daemon.HOST, None, {"task": "do", "window": "ops-../x"}, "queued by host"),
    ("bundle", None, {"task": "do", "window": NEW}, "queued by bundle"),
]


def refusal(body, caller="parent"):
    try:
        daemon.check_terminal(caller, "bundle", body)
    except (ValueError, daemon.Refused) as error:
        return str(error)
    return None


def open_ops(root, caller, body):
    opened = []

    def open_terminal(log, name):
        task = daemon.get_agent_folder(name) / "task.md"
        given = f"on {task.read_text()}" if task.exists() else "with no task"
        opened.append(f"terminal {name if name in (MOVED, RUNNING) else name[:4]} {given}")

    daemon.HOST_FOLDER = "/ops"
    daemon.read_state = lambda: {"queue": []}
    daemon.get_transcript = lambda session, *_: "ops-" + session in (MOVED, RUNNING) or None
    daemon.is_agent_running = lambda bundle: bundle == RUNNING
    daemon.get_agent_folder = lambda name: root / name
    daemon.move_to_tab = lambda log, name: opened.append(f"tab {name}")
    daemon.open_terminal = open_terminal
    daemon.open_window = lambda log, bundle: opened.append(f"window {bundle}")
    daemon.start_job = lambda caller, verb, work, lock: (202, work(None))
    try:
        _code, answer = daemon.open_bundle(caller, {}, {"bundle": "ops", **body})
    except (ValueError, daemon.Refused) as error:
        return str(error)
    return f"held {answer['held']}" if "held" in answer else " ".join(opened)


def open_new(root, agent, start_job):
    """What open --terminal leaves for the terminal of a bundle with no agent: a session, no task.
    It opens while a job of another bundle runs, and after the job of its own bundle."""
    opened = []
    daemon.STATE = root
    daemon.get_worktree_bundle_folder = lambda bundle: str(root)
    daemon.is_made = lambda folder: True
    daemon.open_terminal = lambda log, name: opened.append(
        (daemon.read_text(agent / "session"), (agent / "task.md").read_text()),
    )
    daemon.start_job = start_job
    for path in agent.iterdir():
        path.unlink()
    try:
        with daemon.work_lock, daemon.bundle_locks.setdefault("bundle", threading.Lock()):
            body = {"bundle": "bundle", "terminal": True}
            _code, answer = daemon.open_bundle(daemon.HOST, {}, body)
            time.sleep(0.5)
            early = list(opened)
        daemon.wait_job(daemon.jobs[answer["job"]])
    except daemon.Refused as error:
        return [f"open a bundle with no agent: {error}"]
    if early:
        return [f"open a bundle with no agent: opened during the job of its bundle {early!r}"]
    if len(opened) != 1 or not daemon.SESSION_ID.fullmatch(opened[0][0]) or opened[0][1]:
        return [f"open a bundle with no agent: {opened!r}"]
    return []


def open_twice(root, agent, start_job):
    """A second open --terminal, from a second click on the Agent link while the window of the
    first still starts, opens no other window."""
    opened = []
    windows = []

    def open_terminal(log, name):
        opened.append(name)
        window = [sys.executable, "-c", "import time; time.sleep(30)", "agent-terminator.py", name]
        windows.append(subprocess.Popen(window))

    daemon.STATE = root
    daemon.open_terminal = open_terminal
    daemon.start_job = start_job
    (agent / "session").write_text("x\n")
    (agent / "task.md").write_text("")
    body = {"bundle": "bundle", "terminal": True}
    try:
        for _click in range(2):
            _code, answer = daemon.open_bundle(daemon.HOST, {}, body)
            daemon.wait_job(daemon.jobs[answer["job"]])
    except (ValueError, daemon.Refused) as error:
        return [f"open twice: {error}"]
    finally:
        for window in windows:
            window.kill()
    return [] if opened == ["bundle"] else [f"open twice: opened {opened!r}, expected ['bundle']"]


def open_window(root):
    """A window of a bundle opens for the host, the bundle and its parent only, as a terminal does
    (security check 11-03, B11)."""
    daemon.get_worktree_bundle_folder = lambda bundle: str(root)
    daemon.is_made = lambda folder: True
    daemon.remote_agent.is_remote = lambda folder: False
    daemon.start_job = lambda caller, verb, work, **kwargs: (202, {"job": verb})
    found = []
    for caller in ("other", "parent", "bundle", daemon.HOST):
        try:
            found.append(daemon.open_bundle(caller, {}, {"bundle": "bundle"})[1]["job"])
        except daemon.Refused as error:
            found.append(str(error))
    expected = ["bundle: only the host, the bundle or its parent opens its window", *["open"] * 3]
    return [f"open a window: {found}, expected {expected}"] if found != expected else []


def answer_held(root, start_job):
    """What Seb's yes to the task a bundle sends opens, with the real jobs of the daemon, while a
    job of a bundle runs."""
    opened = []
    daemon.STATE = root
    daemon.open_terminal = lambda log, name: opened.append(
        (daemon.get_agent_folder(name) / "task.md").read_text(),
    )
    daemon.start_job = start_job
    path = daemon.HELD_DIR / "1.json"
    answer = path.with_suffix(".answer")
    answer.write_text(f"yes {daemon.HELD.read_json(path)['digest']}\n")
    with daemon.work_lock:
        daemon.HELD.take(answer, daemon.send_held)
    found = [daemon.HELD.read_json(path.with_suffix(".result"))["text"], *opened]
    expected = [
        "an ops agent window opens on the task of bundle",
        "The agent of the bundle bundle sends this task, and Seb said yes to it in hok:\n\ndo",
    ]
    return [] if found == expected else [f"yes to b1: {found!r}, expected {expected!r}"]


# The held ids go on from b5: OPS_CASES and TASK_CASES hold b1 to b4 before.
HOLD_CASES = [
    (daemon.HOST, {"task": "report"}, "held b5"),
    (daemon.HOST, {"task": "report", "window": NEW}, "held b5"),
    (daemon.HOST, {}, HOLD),
    (daemon.HOST, {"task": " "}, HOLD),
    (daemon.HOST, {"task": ["report"]}, HOLD),
    (daemon.HOST, {"task": "report", "terminal": True}, HOLD),
    (daemon.HOST, {"task": "report", "bundle": "ops"}, HOLD),
    (daemon.HOST, {"task": "x" * 40000}, "task: a text of at most 32768 characters"),
    ("bundle", {"task": "report"}, "held b6"),
]


def hold(caller, body):
    try:
        _code, answer = daemon.hold_ops(caller, {}, body)
    except (ValueError, daemon.Refused) as error:
        return str(error)
    return f"held {answer['held']}"


def answer_hold(root, start_job):
    """What Seb's yes to a task the host held opens: an ops agent on it, told that he said yes."""
    opened = []
    daemon.STATE = root
    daemon.get_agent_folder = lambda name: root / name
    daemon.open_terminal = lambda log, name: opened.append(
        (daemon.get_agent_folder(name) / "task.md").read_text(),
    )
    daemon.start_job = start_job
    path = daemon.HELD_DIR / "5.json"
    answer = path.with_suffix(".answer")
    answer.write_text(f"yes {daemon.HELD.read_json(path)['digest']}\n")
    with daemon.work_lock:
        daemon.HELD.take(answer, daemon.send_held)
    found = opened
    expected = [daemon.HELD_TASK + "report"]
    return [] if found == expected else [f"yes to b5: {found!r}, expected {expected!r}"]


HOLD_TASK = "hold-task: a task, with no terminal"
MADE = "master-made--seb"
MAX_MODEL = "model: one of haiku, opus, sonnet"
NOT_MADE = "half is not made: bctl create or fetch it with its task"
VERIFY = {"task": "verify", "priority": 5, "effort": "high", "model": "sonnet"}
# The caller of open BUNDLE --hold, its body, and the request held or the refusal: whoever asks,
# the host and the parent too. "half" is a bundle not made. The held ids go on from b7.
HOLD_TASK_CASES = [
    (daemon.HOST, {"task": "draft"}, "held b7"),
    (daemon.HOST, {"task": "draft"}, "held b7"),
    (daemon.HOST, VERIFY, "held b8"),
    (daemon.HOST, {"task": daemon.GO_ON, "resume": True}, "held b9"),
    ("other", {"task": "draft"}, "held b10"),
    ("parent", {"task": "draft"}, "held b11"),
    (daemon.HOST, {"bundle": "half", "task": "draft"}, NOT_MADE),
    (daemon.HOST, {}, HOLD_TASK),
    (daemon.HOST, {"task": "draft", "terminal": True}, HOLD_TASK),
    (daemon.HOST, {"task": "draft", "model": "max"}, MAX_MODEL),
    (daemon.HOST, {"task": "draft", "priority": 101}, "priority: an integer from -100 to 100"),
]
PR = "https://github.com/odoo/odoo/pull/300049"
# The caller of fetch on a bundle made already, the bundle that queued its last task, the body,
# and what becomes of the task: as open takes it, or refused with no task.
FETCH_CASES = [
    (daemon.HOST, None, {"name": PR, "task": "do"}, "queued by host"),
    (daemon.HOST, None, {"name": PR}, f"{MADE} is already here, open it instead"),
    ("parent", "parent", {"name": MADE, "task": "do"}, "queued by parent"),
    ("other", "parent", {"name": MADE, "task": "do"}, "held b12"),
    (daemon.HOST, None, {"name": PR, "task": "do", "model": "max"}, MAX_MODEL),
]


def hold_bundle(caller, body):
    try:
        _code, answer = daemon.hold_bundle_task(caller, {}, {"bundle": "bundle", **body})
    except (ValueError, daemon.Refused) as error:
        return str(error)
    return f"held {answer['held']}"


def answer_hold_task(agent):
    """What Seb's yes to the tasks the host held queues: as from the host, not from the parent of
    the bundle, which is told of the verdict no more, with what the task was held with."""
    queued = []
    daemon.enqueue = lambda caller, bundle, body, build=None: queued.append((caller, body))
    (agent / "parent").write_text("parent\n")
    daemon.write_parent("bundle", {"parent": "parent"})
    for number in (7, 8):
        path = daemon.HELD_DIR / f"{number}.json"
        answer = path.with_suffix(".answer")
        answer.write_text(f"yes {daemon.HELD.read_json(path)['digest']}\n")
        daemon.HELD.take(answer, daemon.send_held)
    found = [
        daemon.HELD.read_json(daemon.HELD_DIR / "7.result")["text"],
        *[(caller, body["task"], body.get("priority"), body.get("model")) for caller, body in queued],
        daemon.read_parent("bundle"),
    ]
    expected = [
        "the task of host is queued for bundle",
        (daemon.HOST, daemon.HELD_TASK + "draft", None, None),
        (daemon.HOST, daemon.HELD_TASK + "verify", 5, "sonnet"),
        {},
    ]
    return [] if found == expected else [f"yes to b7 and b8: {found!r}, expected {expected!r}"]


def client_hold():
    """The route bctl open --hold asks: /hold-ops on ops, /hold-task on a bundle with what the task
    is queued with, and the options it refuses."""
    sys.path.insert(0, str(HERE / "client"))
    import bctl

    os.environ.pop("AGENT_WINDOW", None)
    asked = []
    bctl.request = lambda method, path, body=None, **kwargs: asked.append((path, body)) or {
        "bundle": "bundle",
        "held": "b1",
    }
    found = []
    for args in (
        ["ops", "--task", "x"],
        ["bundle", "--task", "x", "--resume", "--priority", "5", "--model", "haiku"],
        ["bundle"],
        ["bundle", "--resume"],
        ["bundle", "--task", "x", "--terminal"],
        ["ops", "--task", "x", "--resume"],
    ):
        sys.argv = ["bctl", "open", *args, "--hold"]
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                bctl.main()
        except SystemExit as error:
            found.append(error.code)
    held = {"bundle": "bundle", "resume": True, "task": "x", "priority": 5, "model": "haiku"}
    expected = [("/hold-ops", {"task": "x"}), ("/hold-task", held)]
    if (asked, found) != (expected, [2] * 4):
        return [f"bctl open --hold: asked {asked!r}, exits {found!r}"]
    return []


def queue_task(agent, queued, caller, parent, body, verb=None):
    (agent / "parent").unlink(missing_ok=True)
    if parent:
        (agent / "parent").write_text(f"{parent}\n")
    (agent / "session").write_text("x\n")
    try:
        code, answer = (verb or daemon.open_bundle)(caller, {}, {"bundle": "bundle", **body})
    except (ValueError, daemon.Refused) as error:
        return str(error)
    if "held" in answer:
        return f"held {answer['held']}"
    return f"queued by {queued[-1][0]}" if code == 202 and queued else repr(answer)


def answer_task(agent, queued):
    """What Seb's yes to the task of b3 queues: the task as from the parent of the bundle."""
    (agent / "parent").write_text("parent\n")
    path = daemon.HELD_DIR / "3.json"
    answer = path.with_suffix(".answer")
    answer.write_text(f"yes {daemon.HELD.read_json(path)['digest']}\n")
    daemon.HELD.take(answer, daemon.send_held)
    found = [daemon.HELD.read_json(path.with_suffix(".result"))["text"], queued[-1]]
    task = "The agent of the bundle other sends this task, and Seb said yes to it in hok:\n\ndo"
    expected = ["the task of other is queued for bundle", ("parent", "bundle", task)]
    return [] if found == expected else [f"yes to b3: {found!r}, expected {expected!r}"]


def get_moved(root):
    """What a move of an ops session to a tab leaves for the ops window, once its agent window
    closed on SIGUSR1: the mode of the session, and the prompt that tells a cut turn to go on."""
    transcript = root / "moved.jsonl"
    entries = [
        {"type": "user", "message": {"content": "go"}, "permissionMode": "plan"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use"}]}},
    ]
    transcript.write_text("".join(f"{json.dumps(entry)}\n" for entry in entries))
    window = "import signal, time; signal.signal(signal.SIGUSR1, lambda *args: exit()); time.sleep(30)"
    terminator = subprocess.Popen([sys.executable, "-c", window, "agent-terminator.py", MOVED])
    daemon.CLAUDE_CONFIG_CONTAINER = str(root / "config")
    daemon.HOST_FOLDER = "/repo/host-ctl"
    daemon.get_transcript = lambda session, *_: transcript
    daemon.is_agent_running = lambda bundle: terminator.poll() is None
    try:
        daemon.move_to_tab(io.StringIO(), MOVED)
    except daemon.Refused as error:
        return [f"move to a tab: {error}"]
    finally:
        terminator.kill()
    storage = root / "config" / "host-ctl" / "extension-storage"
    session = MOVED.removeprefix("ops-")
    found = (
        json.loads((storage / "session-permission-modes" / f"{session}.json").read_text())["mode"],
        (storage / "ops-window-open" / session).read_text(),
    )
    if found != ("plan", daemon.GO_ON):
        return [f"move to a tab: left {found}"]
    return []


def main():
    failures = []
    move_to_tab = daemon.move_to_tab
    start_job = daemon.start_job
    with tempfile.TemporaryDirectory() as root:
        daemon.HELD_DIR = pathlib.Path(root) / "held"
        daemon.PARENTS_DIR = pathlib.Path(root) / "parents"
        daemon.PARENTS_DIR.mkdir()
        daemon.write_parent("bundle", {"parent": "parent"})
        agent = pathlib.Path(root) / "bundle" / ".agent"
        agent.mkdir(parents=True)
        daemon.get_agent_folder = lambda bundle: agent
        daemon.is_held = lambda folder: (folder / "held").exists()
        daemon.get_bundle_container = lambda bundle: "c1"
        for files, window, caller, expected in CASES:
            for path in agent.iterdir():
                path.unlink()
            for name in files:
                (agent / name).write_text("x\n")
            daemon.has_window = lambda container, window=window: window
            found = refusal({"terminal": True}, caller)
            if (found and expected and expected in found) or found == expected:
                continue
            case = f"{sorted(files)} window={window} caller={caller}"
            failures.append(f"{case}: {found!r}, expected {expected!r}")
        for body, expected in BODIES:
            found = refusal(body)
            if found != expected:
                failures.append(f"{body}: {found!r}, expected {expected!r}")
        failures += open_new(pathlib.Path(root), agent, start_job)
        failures += open_twice(pathlib.Path(root), agent, start_job)
        for caller, body, expected in OPS_CASES:
            found = open_ops(pathlib.Path(root), caller, body)
            if found != expected:
                failures.append(f"open ops {caller} {body}: {found!r}, expected {expected!r}")
        failures += answer_held(pathlib.Path(root), start_job)
        failures += open_window(pathlib.Path(root))
        daemon.start_job = start_job
        queued = []
        daemon.get_agent_folder = lambda bundle: agent
        daemon.has_window = lambda container: False
        daemon.read_state = lambda: {"queue": []}
        daemon.enqueue = lambda caller, bundle, body, build=None: queued.append(
            (caller, bundle, body["task"]),
        ) or {"bundle": bundle}
        for caller, parent, body, expected in TASK_CASES:
            found = queue_task(agent, queued, caller, parent, body)
            if found != expected:
                case = f"task of {caller}, parent {parent}"
                failures.append(f"{case}: {found!r}, expected {expected!r}")
        failures += answer_task(agent, queued)
        daemon.move_to_tab = move_to_tab
        failures += get_moved(pathlib.Path(root))
        for caller, body, expected in HOLD_CASES:
            found = hold(caller, body)
            if found != expected:
                failures.append(f"hold-ops {caller} {body}: {found!r}, expected {expected!r}")
        failures += answer_hold(pathlib.Path(root), start_job)
        if daemon.VERBS.get(("POST", "/hold-ops")) is not daemon.hold_ops:
            failures.append("POST /hold-ops is not a route")
        daemon.get_agent_folder = lambda bundle: agent
        daemon.get_worktree_bundle_folder = lambda bundle: str(pathlib.Path(root) / bundle)
        daemon.is_made = lambda folder: pathlib.Path(folder).name != "half"
        for caller, body, expected in HOLD_TASK_CASES:
            found = hold_bundle(caller, body)
            if found != expected:
                failures.append(f"hold-task {caller} {body}: {found!r}, expected {expected!r}")
        daemon.check_disk = lambda: None
        daemon.get_pr = lambda repo, number, fields: {"baseRefName": "master", "headRefName": MADE}
        for caller, parent, body, expected in FETCH_CASES:
            found = queue_task(agent, queued, caller, parent, body, daemon.fetch)
            if found != expected:
                case = f"fetch of {caller}, parent {parent}"
                failures.append(f"{case}: {found!r}, expected {expected!r}")
        failures += answer_hold_task(agent)
        if daemon.VERBS.get(("POST", "/hold-task")) is not daemon.hold_bundle_task:
            failures.append("POST /hold-task is not a route")
        failures += client_hold()
    for line in failures:
        print(line)
    held = len(HOLD_CASES) + len(HOLD_TASK_CASES) + len(FETCH_CASES)
    cases = len(CASES) + len(BODIES) + len(OPS_CASES) + len(TASK_CASES) + held + 11
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
