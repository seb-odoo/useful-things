#!/usr/bin/env python3
"""What open --terminal refuses, and what open ops opens, without podman: python3 test_open.py"""

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon

# The files of the .agent folder, whether its container has a window, the caller and the refusal.
CASES = [
    ({}, False, "other", "bundle has no agent session to open in a terminal"),
    ({"task.md"}, False, "other", "bundle has no agent session to open in a terminal"),
    ({"task.md", "session", "held"}, False, "other", "a terminal holds its session"),
    ({"task.md", "session", "held"}, True, "bundle", "a terminal holds its session"),
    ({"task.md", "session"}, True, "other", "bundle is open in a window"),
    ({"task.md", "session"}, True, "bundle", None),
    ({"task.md", "session"}, True, daemon.HOST, None),
    ({"task.md", "session"}, False, "other", None),
]
BODIES = [
    ({"terminal": True, "task": "do"}, "terminal: only without a task"),
    ({"terminal": True, "resume": True}, "terminal: only without a task"),
]
# The caller, the body of open ops, and what opens or the refusal.
OPS_CASES = [
    ("bundle", {"terminal": True}, "ops: only the host and the ops container open it"),
    (daemon.HOST, {"task": "do", "priority": 1}, "ops: only a task or terminal"),
    (daemon.HOST, {"task": " "}, "task: a text of at most 32768 characters"),
    (daemon.HOST, {}, "window ops"),
    (daemon.HOST, {"terminal": True}, "terminal ops- with no task"),
    (daemon.HOST, {"task": "do"}, "terminal ops- on do"),
]


def refusal(body, caller="other"):
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
        opened.append(f"terminal {name[:4]} {given}")

    daemon.HOST_FOLDER = "/ops"
    daemon.get_agent_folder = lambda name: root / name
    daemon.open_terminal = open_terminal
    daemon.open_window = lambda log, bundle: opened.append(f"window {bundle}")
    daemon.start_job = lambda caller, verb, work: (202, work(None))
    try:
        daemon.open_bundle(caller, {}, {"bundle": "ops", **body})
    except (ValueError, daemon.Refused) as error:
        return str(error)
    return " ".join(opened)


def main():
    failures = []
    with tempfile.TemporaryDirectory() as root:
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
        for caller, body, expected in OPS_CASES:
            found = open_ops(pathlib.Path(root), caller, body)
            if found != expected:
                failures.append(f"open ops {caller} {body}: {found!r}, expected {expected!r}")
    for line in failures:
        print(line)
    cases = len(CASES) + len(BODIES) + len(OPS_CASES)
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
