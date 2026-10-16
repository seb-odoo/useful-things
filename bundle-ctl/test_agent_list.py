#!/usr/bin/env python3
"""What agent-list.py says of each row and of the queue, on .agent/ folders of its own, with a
stand-in for gi, as the ops container has no Gtk: python3 test_agent_list.py"""

import http.server
import importlib.util
import json
import os
import pathlib
import socketserver
import subprocess
import sys
import tempfile
import threading
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
FACETS = [
    (set(), {"fw"}, ["❌ failed 0", "✅ finished 1", "✋ waiting 0", "⏳ running 0", "🚀 starting 0"]),
    ({1}, set(), ["🔧 ops 0", "⏩ fw 1", "🤖 runbot 0", "🎯 task 0", "⭐ mine 0"]),
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
    ("closed", {"done": ("0", 120), "result.md": ("pushed: c4d3b85\n\nmore", 120)}, True,
     "done 2 min ago", "Waiting for you: r+ on odoo#5"),
]
CLOSES = {"closed": "Waiting for you: r+ on odoo#5"}
OPS_INFOS = [
    (True, "", ("", "", 0.0)),
    (False, "", ("shown", "", 0.0)),
    (True, "Nothing left: merged", ("", "Nothing left: merged", 0.0)),
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
NAMES = ["master-x--seb", "Refactor", "master-y--seb"]
USAGE_FIRST = {"master-x--seb": (None, 2 * 1024**3), "0x2": (None, 1250)}
USAGE_NEXT = {"master-x--seb": (50.0, 2 * 1024**3), "0x2": (0.0, 1250)}


def load_list():
    gi = types.ModuleType("gi")
    gi.require_version = lambda name, version: None
    repository = types.ModuleType("gi.repository")
    repository.Gtk = types.SimpleNamespace(Application=object)
    repository.Gdk = repository.GdkX11 = repository.Gio = repository.GLib = repository.Pango = None
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
    if (found := agent_list.get_filters(rows, set(), set())) != FILTERS:
        failures.append(f"filters {found!r}, expected {FILTERS!r}")
    for ranks, kinds, expected in FACETS:
        found = agent_list.get_filters(rows, ranks, kinds)
        if expected not in (found[:5], found[5:10]):
            failures.append(f"filters while {ranks} and {kinds} are pressed: {found!r}")
    for ranks, kinds, expected in FILTERED:
        found = [row.key for row in rows if agent_list.is_kept(row, ranks, kinds)]
        if found != expected:
            failures.append(f"rows kept by {ranks} and {kinds}: {found!r}, expected {expected!r}")
    if (found := [agent_list.get_name(row) for row in rows]) != NAMES:
        failures.append(f"names {found!r}, expected {NAMES!r}")
    title = f"{picker.LOADING} {TITLES[0][0]}"
    row = picker.Row(None, picker.get_rank(TITLES[0][0]), title, "master-x--seb", "0x1", True)
    if (found := agent_list.get_name(row)) != f"{picker.LOADING} master-x--seb":
        failures.append(f"name while VS Code opens {found!r}")
    return failures


def check_ask(agent_list):
    """The answer of the daemon to a request, and the error it gives, on a socket of its own."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            code, answer = (200, {"got": body}) if body.get("bundle") else (400, {"error": "no"})
            data = json.dumps(answer).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        def get_request(self):
            request, _address = super().get_request()
            return request, ("bundle-ctl", 0)

    failures = []
    with tempfile.TemporaryDirectory() as root:
        path = pathlib.Path(root, "ctl.sock")
        server = Server(str(path), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        agent_list.bctl.SOCKETS = (path,)
        for body, expected in (
            ({"bundle": "a"}, {"got": {"bundle": "a"}}),
            ({}, {"error": "400 no"}),
        ):
            if (found := agent_list.ask("POST", "/move", body)) != expected:
                failures.append(f"ask {body!r}: {found!r}, expected {expected!r}")
        server.shutdown()
        server.server_close()
    agent_list.bctl.SOCKETS = (pathlib.Path(root, "gone.sock"),)
    if (found := agent_list.ask("GET", "/queue")) != {"error": "bundle-ctl does not answer"}:
        failures.append(f"ask with no daemon: {found!r}")
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
            if (found := agent_list.picker.get_terminator(child)) != (process.pid, "ops-S1"):
                failures.append(f"terminator {found!r}, expected {(process.pid, 'ops-S1')!r}")
            if (found := agent_list.picker.get_terminator(os.getpid())) is not None:
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
    agent_list.picker.get_terminator = lambda pid: (5, "ops-S1") if pid == "77" else None
    agent_list.get_worktree_bundle_folder = lambda name: f"/bundles/{name}"
    agent_list.read_cgroup = lambda path: cgroups.pop(0)
    agent_list.read_processes = lambda: PROCESSES
    agent_list.picker.read_window_pids = lambda: {"0x2": "77"}
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
    expected = [{"master-x--seb": 2 * 1024**3}] * 2, [["du", "-s", "-k", "/bundles/master-x--seb"]]
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
            found = agent_list.get_info(row, CLOSES.get(name, ""))
            ages = [files[file][1] for file in ("done", "waiting", "run.lock") if file in files]
            since = time.time() - ages[0] if ages else 0.0
            if found[:2] != (state, answer) or abs(found[2] - since) > 5:
                failures.append(f"{name}: {found!r}, expected {(state, answer, since)!r}")
    for minimized, close, expected in OPS_INFOS:
        row = agent_list.picker.Row(None, None, "🔧 Refactor (ops)", "0x0b", "0x0b", minimized)
        if (found := agent_list.get_info(row, close)) != expected:
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
    forced = dict(QUEUE, queue=[dict(QUEUE["queue"][0], forced=True)])
    if (found := agent_list.describe_queue(forced)[1][0][3]) != "a slot, over the cap":
        failures.append(f"forced task waits for {found!r}")
    failures += check_filters(agent_list) + check_reads(agent_list) + check_usage(agent_list)
    failures += check_ask(agent_list)
    for line in failures:
        print(line)
    cases = len(AGENTS) + len(OPS_INFOS) + 4 + 1 + len(FACETS) + len(FILTERED) + 2 + 7 + 3 + 3
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
