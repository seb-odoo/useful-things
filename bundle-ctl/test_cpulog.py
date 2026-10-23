#!/usr/bin/env python3
"""The minute line of cpulog.py, on a cgroup tree and a /proc of its own, and what cpu-report.py
makes of such lines: python3 test_cpulog.py
"""

import importlib.util
import json
import pathlib
import sys
import tempfile
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cpulog

BUNDLE = "a" * 64
GATEWAY = "b" * 64
OPS = "c" * 64
MANAGER = "user.slice/user-1001.slice/user@1001.service"
CGROUPS = {
    "system.slice": 0,
    "init.scope": 0,
    "user.slice/user-120.slice": 0,
    "user.slice/user-1001.slice/session-2.scope": 0,
    f"{MANAGER}/init.scope": 0,
    f"{MANAGER}/session.slice": 0,
    f"{MANAGER}/app.slice/snap.firefox.firefox-0d3b9a1e-1c2d-4e5f-8a9b-0c1d2e3f4a5b.scope": 0,
    f"{MANAGER}/app.slice/app-com.google.Chrome-3221453.scope": 0,
    f"{MANAGER}/app.slice/run-rac484593bc8d4bc98f6b82e5efb6b3fb.service": 0,
    f"{MANAGER}/app.slice/bundle-ctl.service": 0,
    f"{MANAGER}/user.slice/libpod-{BUNDLE}.scope": 0,
    f"{MANAGER}/user.slice/libpod-{GATEWAY}.scope": 0,
    f"{MANAGER}/user.slice/libpod-{OPS}.scope": 0,
    f"{MANAGER}/user.slice/rootless-netns-d8c29449.scope": 0,
}
PROCESSES = {
    7: ("htop", "", "user.slice/user-1001.slice/session-2.scope"),
    50: ("terminator", "/usr/bin/python3\0/usr/bin/terminator\0", f"{MANAGER}/app.slice/run-rac484593bc8d4bc98f6b82e5efb6b3fb.service"),
    51: ("bash", "bash\0", f"{MANAGER}/app.slice/run-rac484593bc8d4bc98f6b82e5efb6b3fb.service"),
    60: ("git", "git\0log\0-Sx\0--all\0", f"{MANAGER}/user.slice/libpod-{BUNDLE}.scope/container"),
    94: ("kcompactd0", "", ""),
}


def write(root, proc, seconds, ticks, busy, idle):
    """The cgroups at their CPU seconds, the processes at their ticks, the machine at its busy and
    idle ticks."""
    for path, value in seconds.items():
        folder = root / path
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "cpu.stat").write_text(f"usage_usec {int(value * 1e6)}\nuser_usec 0\n")
        (folder / "cgroup.procs").write_text(
            "".join(f"{pid}\n" for pid, (*_rest, cgroup) in PROCESSES.items() if cgroup == path)
        )
    (proc / "pressure").mkdir(parents=True, exist_ok=True)
    (proc / "pressure" / "cpu").write_text("some avg10=30.10 avg60=32.41 avg300=31.68 total=1\n")
    (proc / "stat").write_text(f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\n")
    for pid, (comm, args, cgroup) in PROCESSES.items():
        folder = proc / str(pid)
        folder.mkdir(exist_ok=True)
        fields = ["S", "1"] + ["0"] * 9 + [str(ticks.get(pid, 0)), "0"] + ["0"] * 6 + ["100"]
        (folder / "stat").write_text(f"{pid} ({comm}) {' '.join(fields)}\n")
        (folder / "cmdline").write_text(args)
        (folder / "comm").write_text(f"{comm}\n")
        (folder / "cgroup").write_text(f"0::/{cgroup}\n")


def inspect(ids):
    folders = {
        BUNDLE: ("busy_bee", "/home/x/src/odoo/master/master-x--seb"),
        GATEWAY: ("github-api-proxy", ""),
        OPS: ("sharp_jang", "/home/x/repo/host-ctl"),
    }
    return {id_: folders[id_] for id_ in ids}


def get_line():
    """The line of a minute where the bundle took 3 threads (the git log 2 of them), htop and
    the terminator window 0.5 each, the desktop 1, the gateway 0.25, on 6 threads busy of 12."""
    with tempfile.TemporaryDirectory() as folder:
        root, proc = pathlib.Path(folder, "cgroup"), pathlib.Path(folder, "proc")
        write(root, proc, CGROUPS, {}, 0, 1000)
        log = cpulog.CpuLog("/home/x/src/odoo/", root, proc, 1001, inspect, 12)
        with mock.patch("time.monotonic", side_effect=[0, 60]):
            first = log.line()
            after = dict(CGROUPS)
            after[f"{MANAGER}/user.slice/libpod-{BUNDLE}.scope"] = 180
            after[f"{MANAGER}/user.slice/libpod-{GATEWAY}.scope"] = 15
            after["user.slice/user-1001.slice/session-2.scope"] = 60
            after[f"{MANAGER}/app.slice/run-rac484593bc8d4bc98f6b82e5efb6b3fb.service"] = 30
            ticks = {7: 24 * cpulog.TICK, 50: 30 * cpulog.TICK, 60: 120 * cpulog.TICK}
            write(root, proc, after, ticks, 500, 1500)
            return first, log.line(["master-x--seb"], {"master-x--seb": "hoot"})


def load_report():
    spec = importlib.util.spec_from_file_location("report", HERE / "cpu-report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    failures = []
    first, line = get_line()
    data = json.loads(line.removeprefix("cpu "))
    report = load_report()
    stamp = "2026-10-23T16:02:58+0200 seb-laptop python[3314192]: "
    entries = report.parse([stamp + line, stamp.replace("16:02", "16:03") + line])
    totals = report.sum_up(entries, "--seb")
    cases = [
        ("no line on the first sample", first, None),
        ("the busy threads", data["busy"], 6.0),
        ("the wait on the CPU", data["wait"], 32.41),
        ("a bundle by its folder", data["containers"].get("master-x--seb"), 3.0),
        ("a gateway by its name", data["containers"].get("gateway:github-api-proxy"), 0.25),
        ("an idle container is left out", "ops" in data["containers"], False),
        ("the desktop", data["host"].get("desktop"), 1.0),
        ("a transient unit by its busiest process", data["host"].get("terminator"), 0.5),
        ("the kernel gets what is left", data["host"].get("kernel"), 1.25),
        ("the agents and the runs", (data["agents"], data["runs"]), (["master-x--seb"], {"master-x--seb": "hoot"})),
        (
            "the top processes",
            data["top"],
            [
                [2.0, "master-x--seb", "git log -Sx --all"],
                [0.5, "terminator", "/usr/bin/python3 /usr/bin/terminator"],
                [0.4, "desktop", "htop"],
            ],
        ),
        ("the report counts the minutes", totals["minutes"], 2),
        ("the report splits by kind and use", totals["kinds"].get(("mine", "test")), 3.0),
        ("the report names the gateways", totals["kinds"].get(("gateway", "")), 0.25),
    ]
    for name, found, expected in cases:
        if found != expected:
            failures.append(f"{name}: {found!r}, expected {expected!r}")
    for line in failures:
        print(line)
    print(f"{len(cases) - len(failures)}/{len(cases)} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
