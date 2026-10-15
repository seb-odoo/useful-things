#!/usr/bin/env python3
"""What agent-list.py says of each row and of the queue, on .agent/ folders of its own, with a
stand-in for gi, as the ops container has no Gtk: python3 test_agent_list.py"""

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import types

FILTERS = [
    "❌ failed 0",
    "✅ finished 1",
    "✋ waiting 0",
    "⏳ running 1",
    "🚀 starting 0",
    "🔧 ops 1",
    "⏩ fw 1",
    "🤖 runbot 0",
    "🎯 task 0",
    "⭐ mine 1",
    "🧠 analysis 0",
    "📝 review 0",
]
FILTERED = [
    (set(), set(), ["fw", "ops", "mine"]),
    ({1}, set(), ["fw"]),
    (set(), {"mine", "ops"}, ["ops", "mine"]),
    ({1, 3}, {"ops"}, ["ops"]),
]
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
PROCESSES = {
    1: (0, 1.0, 100, ["/sbin/init"]),
    10: (1, 2.0, 1000, ["/x/native-binary/claude", "--session-id", "S1", "--model", "opus"]),
    11: (10, 0.5, 200, ["bash", "-c", "pytest"]),
    12: (11, 0.25, 50, ["python3"]),
    20: (1, 4.0, 3000, ["claude", "--resume", "S2"]),
    30: (1, 1.0, 10, ["podman", "exec", "ops", "claude", "--session-id", "S1"]),
}
TERMINATOR = """\
import subprocess
child = subprocess.Popen(["sleep", "30"])
print(child.pid, flush=True)
child.wait()
"""
TITLES = [
    ("✅ ⏩ master-x--seb (fw)", "fw"),
    ("⏳ 🔧 Refactor (ops)", "ops"),
    ("⭐ master-y--seb (mine)", "mine"),
]
USAGE_FIRST = {"master-x--seb": ("", "2.0 GB"), "0x2": ("", "0.0 GB")}
USAGE_NEXT = {"master-x--seb": ("50%", "2.0 GB"), "0x2": ("0%", "0.0 GB")}


def load_list():
    gi = types.ModuleType("gi")
    gi.require_version = lambda name, version: None
    repository = types.ModuleType("gi.repository")
    repository.Gtk = types.SimpleNamespace(Application=object)
    repository.Gdk = repository.GdkX11 = repository.GLib = repository.Pango = None
    gi.repository = repository
    sys.modules.update({"gi": gi, "gi.repository": repository})
    spec = importlib.util.spec_from_file_location("agent_list", HERE / "agent-list.py")
    agent_list = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent_list)
    return agent_list


def check_filters(agent_list):
    picker = agent_list.picker
    rows = [
        picker.Row(None, picker.get_rank(title), title, key, key, True) for title, key in TITLES
    ]
    failures = []
    if (found := agent_list.get_filters(rows)) != FILTERS:
        failures.append(f"filters {found!r}, expected {FILTERS!r}")
    for ranks, kinds, expected in FILTERED:
        found = [row.key for row in rows if agent_list.is_kept(row, ranks, kinds)]
        if found != expected:
            failures.append(f"rows kept by {ranks} and {kinds}: {found!r}, expected {expected!r}")
    return failures


def check_reads(agent_list):
    """What a cgroup, a process tree and the parent of a terminator read as, on files and
    processes of their own."""
    failures = []
    for session, expected in (("S1", (2.75, 1250)), ("S2", (4.0, 3000)), ("S3", None)):
        if (found := agent_list.read_tree(PROCESSES, session)) != expected:
            failures.append(f"tree of {session}: {found!r}, expected {expected!r}")
    with tempfile.TemporaryDirectory() as root:
        cgroup = pathlib.Path(root)
        (cgroup / "cpu.stat").write_text("usage_usec 2500000\nuser_usec 2000000\n")
        (cgroup / "memory.current").write_text("6000\n")
        (cgroup / "memory.stat").write_text("anon 1000\nfile 5000\nshmem 300\n")
        if (found := agent_list.read_cgroup(cgroup)) != (2.5, 1300):
            failures.append(f"cgroup {found!r}, expected (2.5, 1300)")
        if (found := agent_list.read_cgroup(cgroup / "gone")) is not None:
            failures.append(f"cgroup gone: {found!r}")
        script = pathlib.Path(root, "agent-terminator.py")
        script.write_text(TERMINATOR)
        command = [sys.executable, script, "ops-S1"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
        try:
            child = process.stdout.readline().strip()
            if (found := agent_list.get_terminator(child)) != (process.pid, "ops-S1"):
                failures.append(f"terminator {found!r}, expected {(process.pid, 'ops-S1')!r}")
            if (found := agent_list.get_terminator(os.getpid())) is not None:
                failures.append(f"terminator of another window: {found!r}")
        finally:
            process.kill()
            process.wait()
    return failures


def check_usage(agent_list):
    """The CPU and RAM of a bundle row and of an ops row over two reads 5 seconds apart, then the
    disk of the bundle, measured once."""
    clock = [100.0]
    cgroups = [(10.0, 2 * 1024**3), (12.5, 2 * 1024**3)]
    agent_list.get_terminator = lambda pid: (5, "ops-S1") if pid == "77" else None
    agent_list.get_worktree_bundle_folder = lambda name: f"/bundles/{name}"
    agent_list.read_cgroup = lambda path: cgroups.pop(0)
    agent_list.read_processes = lambda: PROCESSES
    agent_list.read_window_pids = lambda: {"0x2": "77"}
    agent_list.time = types.SimpleNamespace(monotonic=lambda: clock[0], time=time.time)
    usage = agent_list.Usage()
    usage.get_containers = lambda: {"/bundles/master-x--seb": "cgroup"}
    usage.keys = [("master-x--seb", "0x1"), ("0x2", "0x2")]
    failures = []
    for expected in (USAGE_FIRST, USAGE_NEXT):
        if (found := usage.read()) != expected:
            failures.append(f"usage {found!r}, expected {expected!r}")
        clock[0] += 5
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return types.SimpleNamespace(stdout=f"{2 * 1024**2}\t{command[-1]}\n")

    agent_list.subprocess = types.SimpleNamespace(run=run)
    disks = [usage.read_disks(), usage.read_disks()]
    expected = [{"master-x--seb": "2.0 GB"}] * 2, [["du", "-s", "-k", "/bundles/master-x--seb"]]
    if (disks, commands) != expected:
        failures.append(f"disks {disks!r} by {commands!r}, expected {expected!r}")
    return failures


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
    failures += check_filters(agent_list) + check_reads(agent_list) + check_usage(agent_list)
    for line in failures:
        print(line)
    cases = len(AGENTS) + 5 + len(FILTERED) + 1 + 7 + 3
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
