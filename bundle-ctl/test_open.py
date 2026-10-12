#!/usr/bin/env python3
"""What open --terminal refuses, without podman: python3 test_open.py"""

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon

# The files of the .agent folder, whether its container has a window, and the refusal.
CASES = [
    ({}, False, "bundle has no agent session to open in a terminal"),
    ({"task.md"}, False, "bundle has no agent session to open in a terminal"),
    ({"task.md", "session", "held"}, False, "a terminal holds its session"),
    ({"task.md", "session"}, True, "bundle is open in a window"),
    ({"task.md", "session"}, False, None),
]
BODIES = [
    ({"terminal": True, "task": "do"}, "terminal: only without a task"),
    ({"terminal": True, "resume": True}, "terminal: only without a task"),
]


def refusal(body):
    try:
        daemon.check_terminal("bundle", body)
    except (ValueError, daemon.Refused) as error:
        return str(error)
    return None


def main():
    failures = []
    with tempfile.TemporaryDirectory() as root:
        agent = pathlib.Path(root) / "bundle" / ".agent"
        agent.mkdir(parents=True)
        daemon.get_agent_folder = lambda bundle: agent
        daemon.is_held = lambda folder: (folder / "held").exists()
        daemon.get_bundle_container = lambda bundle: "c1"
        for files, window, expected in CASES:
            for path in agent.iterdir():
                path.unlink()
            for name in files:
                (agent / name).write_text("x\n")
            daemon.has_window = lambda container, window=window: window
            found = refusal({"terminal": True})
            if (found and expected and expected in found) or found == expected:
                continue
            failures.append(f"{sorted(files)} window={window}: {found!r}, expected {expected!r}")
        for body, expected in BODIES:
            found = refusal(body)
            if found != expected:
                failures.append(f"{body}: {found!r}, expected {expected!r}")
    for line in failures:
        print(line)
    cases = len(CASES) + len(BODIES)
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
