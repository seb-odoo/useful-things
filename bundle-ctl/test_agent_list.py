#!/usr/bin/env python3
"""What agent-list.py says of each row and of the queue, on .agent/ folders of its own, with a
stand-in for gi, as the ops container has no Gtk: python3 test_agent_list.py"""

import importlib.util
import os
import pathlib
import sys
import tempfile
import time
import types

HERE = pathlib.Path(__file__).resolve().parent
MEMORY = {"available": 3.1, "room": 0.4, "short": True, "stall": 7, "swap": 9.0}
QUEUE = {
    "agents": 8,
    "max_agents": 15,
    "memory": dict(MEMORY, short=False),
    "queue": [{"bundle": "master-x--seb", "priority": 5, "queued": "14:02", "waits_for": "a slot"}],
}
AGENTS = [
    ("done", {"done": ("0", 720), "result.md": ("needs Seb: the test moved\n\nmore", 720)}, True,
     "done 12 min ago", "needs Seb: the test moved"),
    ("failed", {"done": ("failed", 30), "result.md": ("devcontainer up failed", 30)}, True,
     "failed 30 s ago", "devcontainer up failed"),
    ("waiting", {"waiting": ("", 300), "run.lock": ("", 900)}, False, "waiting for 5 min, shown", ""),
    ("running", {"run.lock": ("", 180)}, True, "running for 3 min", ""),
    ("starting", {}, True, "starting", ""),
]


def load_list():
    gi = types.ModuleType("gi")
    gi.require_version = lambda name, version: None
    repository = types.ModuleType("gi.repository")
    repository.Gtk = types.SimpleNamespace(Application=object)
    repository.GdkX11 = repository.GLib = repository.Pango = None
    gi.repository = repository
    sys.modules.update({"gi": gi, "gi.repository": repository})
    spec = importlib.util.spec_from_file_location("agent_list", HERE / "agent-list.py")
    agent_list = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent_list)
    return agent_list


def main():
    failures = []
    agent_list = load_list()
    with tempfile.TemporaryDirectory() as root:
        agent_list.get_agent_folder = lambda name: pathlib.Path(root, name, ".agent")
        for name, files, minimized, state, answer in AGENTS:
            folder = pathlib.Path(root, name, ".agent")
            folder.mkdir(parents=True)
            for file, (text, age) in files.items():
                (folder / file).write_text(text)
                os.utime(folder / file, (time.time() - age,) * 2)
            row = agent_list.picker.Row(None, None, name, name, "0x01", minimized)
            if (found := agent_list.get_info(row)) != (state, answer):
                failures.append(f"{name}: {found!r}, expected {(state, answer)!r}")
    for minimized, expected in ((True, ("", "")), (False, ("shown", ""))):
        row = agent_list.picker.Row(None, None, "🔧 Refactor (ops)", "0x0b", "0x0b", minimized)
        if (found := agent_list.get_info(row)) != expected:
            failures.append(f"ops row, minimized {minimized}: {found!r}, expected {expected!r}")
    queue = (
        "8/15 agents at work in a terminal, 1 queued",
        [["master-x--seb", "5", "14:02", "a slot"]],
    )
    if (found := agent_list.describe_queue(QUEUE)) != queue:
        failures.append(f"queue {found!r}, expected {queue!r}")
    head = agent_list.describe_queue(dict(QUEUE, memory=MEMORY))[0]
    if "; the host is short of memory: 3.1 GB available" not in head:
        failures.append(f"queue while the host is short of memory: {head!r}")
    if (found := agent_list.describe_queue(None)) != ("bundle-ctl does not answer", []):
        failures.append(f"queue with no daemon: {found!r}")
    for line in failures:
        print(line)
    cases = len(AGENTS) + 5
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
