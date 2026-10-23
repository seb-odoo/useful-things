"""The CPU of the host each minute, as one line of the journal of the daemon: `cpu {...}`.

- busy: the threads at work on average, of the whole machine; wait: the percent of the last
  minute tasks waited on the CPU.
- containers: the threads of each container, by bundle; `ops` for a container opened on another
  folder, `gateway:NAME` for one of no folder. A cgroup counts the short commands too (a test, a
  `git log`), which no sample of the processes sees.
- host: the threads of the rest: `desktop` (Xorg, Cinnamon and what the login started), each unit
  of app.slice, `session.slice`, `system`, `other users`, `podman network`, and `kernel` for what
  is left of busy.
- agents: the bundles whose agent is at work; runs: what each bundle runs in the run queue.
- top: the processes that took TOP_SECONDS or more of the minute, the busiest first, as
  [threads, where, command line].
- cost: the CPU seconds the daemon took to make the line.

cpu-report.py sums these lines.
"""

import json
import os
import pathlib
import re
import subprocess
import time

import runs

ARGS = 120
CGROUP = pathlib.Path("/sys/fs/cgroup")
MIN_THREADS = 0.01
PROC = pathlib.Path("/proc")
SCOPE = re.compile(r"libpod-([0-9a-f]{64})\.scope")
SUFFIX = re.compile(
    r"(-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}|-[0-9a-f]{32}|-\d+)?\.(scope|service|slice)$"
)
TICK = os.sysconf("SC_CLK_TCK")
TOP = 6
TOP_SECONDS = 3
TRANSIENT = ("run-r", "vte-spawn-")


def read_usage(path):
    """The CPU seconds of a cgroup since it began, None once it is gone."""
    try:
        with open(path / "cpu.stat") as file:
            return int(file.readline().split()[1]) / 1e6
    except (IndexError, OSError, ValueError):
        return None


def read_busy(proc=PROC):
    """The ticks the CPUs worked since boot, and all the ticks."""
    with open(proc / "stat") as file:
        fields = [int(value) for value in file.readline().split()[1:9]]
    return sum(fields) - fields[3] - fields[4], sum(fields)


def read_processes(proc=PROC):
    """The CPU ticks of each process, by its pid and its start time."""
    found = {}
    for entry in os.scandir(proc):
        if not entry.name.isdigit():
            continue
        try:
            with open(f"{entry.path}/stat", "rb") as file:
                fields = file.read().rpartition(b")")[2].split()
        except OSError:
            continue
        found[int(entry.name), int(fields[19])] = int(fields[11]) + int(fields[12])
    return found


def get_cgroups(root, uid):
    """The cgroups measured: the children of the cgroups that hold the user's session, his manager,
    its apps and its containers, but not those themselves."""
    user = root / "user.slice" / f"user-{uid}.slice"
    manager = user / f"user@{uid}.service"
    split = {root, root / "user.slice", user, manager, manager / "app.slice", manager / "user.slice"}
    found, todo = [], [root]
    while todo:
        try:
            children = [pathlib.Path(entry.path) for entry in os.scandir(todo.pop()) if entry.is_dir()]
        except OSError:
            continue
        found += [path for path in children if path not in split]
        todo += [path for path in children if path in split]
    return found


def get_label(path, root, uid):
    """The part of the host of a cgroup, or the id of its container. None for a transient unit,
    named after its busiest process."""
    name = path.name
    if match := SCOPE.fullmatch(name):
        return match[1]
    if name.startswith(TRANSIENT):
        return None
    if name.startswith("session-") and name.endswith(".scope"):
        return "desktop"
    if path.parent == root / "user.slice":
        return "other users"
    if path.parent == root:
        return "system"
    if name.startswith("rootless-netns-"):
        return "podman network"
    if name == "init.scope":
        return "systemd --user"
    return SUFFIX.sub("", name)


def inspect_containers(ids):
    """The name and the folder of each container, by its id."""
    format_ = '{{.Id}} {{.Name}} {{index .Config.Labels "devcontainer.local_folder"}}'
    out = subprocess.run(
        ["podman", "inspect", "--format", format_, *ids],
        capture_output=True,
        check=False,
        text=True,
    ).stdout
    found = {}
    for line in out.splitlines():
        id_, name, folder = (line.split(" ", 2) + [""])[:3]
        found[id_] = (name, folder.strip())
    return found


def read_args(pid, proc=PROC):
    try:
        args = (proc / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(args.replace(b"\0", b" ").decode(errors="replace").split())[:ARGS]


def read_comm(pid, proc=PROC):
    try:
        return (proc / str(pid) / "comm").read_text().strip()
    except OSError:
        return "?"


def read_pids(path):
    try:
        return [int(pid) for pid in (path / "cgroup.procs").read_text().split()]
    except OSError:
        return []


def read_cgroup_path(pid, root, proc=PROC):
    try:
        line = (proc / str(pid) / "cgroup").read_text().splitlines()[0]
    except (IndexError, OSError):
        return None
    return pathlib.Path(f"{root}{line.partition('::')[2]}".rstrip("/"))


def get_threads(after, before, seconds):
    return {key: (value - before.get(key, 0)) / seconds for key, value in after.items()}


def keep(values):
    """The values worth a place on the line, the largest first."""
    found = sorted(((key, round(value, 2)) for key, value in values.items()), key=lambda x: -x[1])
    return {key: value for key, value in found if value >= MIN_THREADS}


class CpuLog:
    """Each line compares a sample with the one before, so the first call gives none."""

    def __init__(
        self,
        bundles_root,
        root=CGROUP,
        proc=PROC,
        uid=None,
        inspect=inspect_containers,
        cpus=None,
    ):
        self.bundles_root = f"{bundles_root.rstrip('/')}/"
        self.cpus = cpus or os.cpu_count()
        self.inspect = inspect
        self.labels = {}
        self.proc = proc
        self.root = root
        self.sample = None
        self.uid = os.getuid() if uid is None else uid

    def read(self):
        cgroups = get_cgroups(self.root, self.uid)
        usage = {path: read_usage(path) for path in cgroups}
        return {
            "at": time.monotonic(),
            "busy": read_busy(self.proc),
            "processes": read_processes(self.proc),
            "usage": {path: value for path, value in usage.items() if value is not None},
        }

    def name_container(self, id_):
        """bundles_root holds the bundles, named after their folder."""
        name, folder = self.labels.get(id_, (id_[:12], ""))
        if folder.startswith(self.bundles_root):
            return os.path.basename(folder)
        return "ops" if folder else f"gateway:{name}"

    def line(self, agents=(), runs_=None):
        """The line of the minute since the last call, None on the first."""
        start = time.thread_time()
        before, after = self.sample, self.read()
        self.sample = after
        if before is None:
            return None
        seconds = after["at"] - before["at"]
        worked, total = (a - b for a, b in zip(after["busy"], before["busy"], strict=True))
        busy = worked / total * self.cpus if total else 0.0
        ticks = get_threads(after["processes"], before["processes"], seconds * TICK)
        threads = get_threads(after["usage"], before["usage"], seconds)
        labels = {path: get_label(path, self.root, self.uid) for path in threads}
        ids = {label for label in labels.values() if label and len(label) == 64}
        if unknown := ids - self.labels.keys():
            self.labels.update(self.inspect(sorted(unknown)))
        by_pid = {pid: value for (pid, _start), value in ticks.items()}
        for path, label in labels.items():
            if label is None:
                pids = read_pids(path) or [0]
                labels[path] = read_comm(max(pids, key=lambda pid: by_pid.get(pid, 0)), self.proc)
            elif label in ids:
                labels[path] = self.name_container(label)
        containers, host = {}, {}
        for path, value in threads.items():
            part = containers if SCOPE.fullmatch(path.name) else host
            part[labels[path]] = part.get(labels[path], 0) + value
        host["kernel"] = max(0.0, busy - sum(threads.values()))
        top = []
        for (pid, _start), value in sorted(ticks.items(), key=lambda item: -item[1])[:TOP]:
            if value * seconds < TOP_SECONDS:
                break
            path = read_cgroup_path(pid, self.root, self.proc)
            while path and path not in labels and path != self.root:
                path = path.parent
            where = labels.get(path, "kernel")
            top.append([round(value, 2), where, read_args(pid, self.proc) or read_comm(pid, self.proc)])
        return "cpu " + json.dumps(
            {
                "busy": round(busy, 2),
                "cpus": self.cpus,
                "wait": runs.read_stall(60, self.proc / "pressure" / "cpu"),
                "containers": keep(containers),
                "host": keep(host),
                "agents": sorted(agents),
                "runs": runs_ or {},
                "top": top,
                "cost": round(time.thread_time() - start, 3),
            },
            separators=(",", ":"),
        )
