"""The run queue: a heavy command of a container waits here for cores of its own and for memory.

A run is a test run, an install, a server: what a hook of the bundles (`client/run-hook.py`) or
`bctl run` announces before it starts. The pool is the cores left once the first ones are kept for
the desktop. A run holds its cores from its start until its process ends, the next ones wait in
arrival order, and the client pins the run to the CPUs it was given.
"""

import json
import os
import pathlib
import re
import select
import socket
import struct
import threading
import time

CPU_ROOT = pathlib.Path("/sys/devices/system/cpu")
GIGABYTE = 1024**3
HOLD = 60 * 60
KEEPALIVE = 20
MAX_ANCESTORS = 32
MAX_CORES = 2
MAX_LABEL = 200
MAX_STALL = 5
MAX_WORKERS = 32
RAMP = 60
SHARED = ("server", "shell")
TICKET = 15 * 60
KINDS = ("hoot", "odoo", "other", *SHARED)


def parse_cpus(text):
    """The CPUs of a kernel list such as `0-3,8`."""
    cpus = set()
    for part in filter(None, text.strip().split(",")):
        first, _, last = part.partition("-")
        cpus.update(range(int(first), int(last or first) + 1))
    return cpus


def format_cpus(cpus):
    parts = []
    for cpu in sorted(cpus):
        if parts and parts[-1][1] == cpu - 1:
            parts[-1][1] = cpu
        else:
            parts.append([cpu, cpu])
    return ",".join(str(first) if first == last else f"{first}-{last}" for first, last in parts)


def read_cores():
    """Every core of the machine, as the tuple of its CPUs."""
    cores = set()
    for path in CPU_ROOT.glob("cpu[0-9]*/topology/thread_siblings_list"):
        cores.add(tuple(sorted(parse_cpus(path.read_text()))))
    return sorted(cores) or [(cpu,) for cpu in sorted(os.sched_getaffinity(0))]


def get_pool(cores, spec=None):
    """The cores the runs share: the ones `spec` holds whole, else all but the first third."""
    if spec:
        cpus = parse_cpus(spec)
        return [core for core in cores if cpus.issuperset(core)] or cores
    return cores[max(1, len(cores) // 3) :] or cores


def get_need(kind, workers):
    """The cores a run holds and the gigabytes it is expected to take."""
    if kind == "hoot":
        return min(workers, MAX_CORES), 1 + 1.3 * workers
    if kind in SHARED:
        return 0, 0.6
    return 1, 2 if kind == "odoo" else 1


def read_stat(pid):
    """The state, parent and start time of a process, None when it is gone."""
    try:
        fields = pathlib.Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
    except OSError:
        return None
    return fields[0], int(fields[1]), int(fields[19])


def get_birth(pid):
    stat = read_stat(pid)
    return stat[2] if stat and stat[0] != "Z" else None


def get_parent(pid):
    stat = read_stat(pid)
    return stat[1] if stat else 0


def read_memory():
    """The gigabytes available, and the percent of the last 10 seconds tasks stalled on memory."""
    meminfo = pathlib.Path("/proc/meminfo").read_text()
    available = int(re.search(r"MemAvailable:\s+(\d+)", meminfo)[1])
    return available * 1024 / GIGABYTE, read_stall()


def read_stall(seconds=10):
    """The percent of the last 10, 60 or 300 seconds tasks stalled on memory."""
    try:
        pressure = pathlib.Path("/proc/pressure/memory").read_text()
        return float(re.search(rf"full avg{seconds}=([\d.]+)", pressure)[1])
    except (OSError, TypeError):
        return 0.0


def read_swap():
    """The gigabytes in swap."""
    meminfo = pathlib.Path("/proc/meminfo").read_text()
    total = int(re.search(r"SwapTotal:\s+(\d+)", meminfo)[1])
    free = int(re.search(r"SwapFree:\s+(\d+)", meminfo)[1])
    return (total - free) * 1024 / GIGABYTE


def get_cgroup(pid):
    try:
        line = pathlib.Path(f"/proc/{pid}/cgroup").read_text().splitlines()[0]
    except (IndexError, OSError):
        return None
    return f"/sys/fs/cgroup{line.partition('::')[2]}"


def read_cpu_seconds(cgroup):
    try:
        stat = pathlib.Path(cgroup or "", "cpu.stat").read_text()
    except OSError:
        return None
    return int(re.search(r"usage_usec (\d+)", stat)[1]) / 1e6


class RunQueue:
    def __init__(self, cores, min_free, path=None, read_memory=read_memory, get_birth=get_birth):
        self.changed = threading.Condition()
        self.cores = cores
        self.get_birth = get_birth
        self.min_free = min_free
        self.path = path
        self.read_memory = read_memory
        self.running = []
        self.tickets = {}
        self.waiting = []
        self.load()

    def load(self):
        try:
            runs = json.loads(self.path.read_text())
        except (AttributeError, OSError, ValueError):
            return
        self.running = [run for run in runs if self.is_alive(run)]

    def is_alive(self, run):
        return run["birth"] is not None and self.get_birth(run["pid"]) == run["birth"]

    def save(self):
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.running, indent=1))
            tmp.replace(self.path)

    def find_holder(self, pid):
        """The run with cores of its own whose process is `pid` or one of its ancestors."""
        by_pid = {run["pid"]: run for run in self.running if run["need"]}
        for _ in range(MAX_ANCESTORS):
            if pid in by_pid:
                return by_pid[pid]
            pid = get_parent(pid)
            if pid <= 1:
                return None
        return None

    def request(self, bundle, pid, kind, workers, label):
        """Queue a run for the process `pid`, or start it at once. A command of a run that already
        holds cores gets the same ones.
        """
        now = time.time()
        with self.changed:
            holder = self.find_holder(pid)
            if holder:
                return dict(holder, nested=True)
            need, gigabytes = get_need(kind, workers)
            self.tickets = {key: at for key, at in self.tickets.items() if now - at < TICKET}
            run = {
                "birth": self.get_birth(pid),
                "bundle": bundle,
                "cgroup": get_cgroup(pid),
                "cpus": None,
                "gigabytes": gigabytes,
                "kind": kind,
                "label": label,
                "need": min(need, len(self.cores)),
                "pid": pid,
                "queued": self.tickets.pop((bundle, kind, label), now),
                "started": None,
                "workers": workers,
            }
            self.waiting.append(run)
            self.waiting.sort(key=lambda run: run["queued"])
            self.admit()
            return run

    def holds_cores(self, run):
        """Whether a run keeps its cores to itself. After `HOLD` it lends them: a run nobody
        ends must not stop the queue.
        """
        return bool(run["need"]) and time.time() - run["started"] < HOLD

    def get_free_cores(self):
        taken = {cpu for run in self.running if self.holds_cores(run) for cpu in run["cpus"]}
        return [core for core in self.cores if not taken.intersection(core)]

    def get_blocker(self, run):
        """Why a run cannot start now: `cores`, `memory`, or None."""
        if not run["need"]:
            return None
        if len(self.get_free_cores()) < run["need"]:
            return "cores"
        if not any(self.holds_cores(other) for other in self.running):
            return None
        now = time.time()
        available, stall = self.read_memory()
        ramping = sum(
            other["gigabytes"] for other in self.running if now - other["started"] < RAMP
        )
        if stall >= MAX_STALL or available - ramping < run["gigabytes"] + self.min_free:
            return "memory"
        return None

    def admit(self):
        """Start the runs that fit, in arrival order: a run that waits holds back the next ones,
        except the ones that take no core of their own.
        """
        blocked = False
        for run in list(self.waiting):
            run["blocker"] = "queue" if blocked and run["need"] else self.get_blocker(run)
            if run["blocker"]:
                blocked = True
                continue
            cores = self.get_free_cores()[: run["need"]] or self.cores
            run.update(
                cpu_seconds=read_cpu_seconds(run["cgroup"]),
                cpus=sorted(cpu for core in cores for cpu in core),
                started=time.time(),
            )
            self.waiting.remove(run)
            self.running.append(run)
            waited = run["started"] - run["queued"]
            print(
                f"run started {run['bundle']} {run['kind']} cpus={format_cpus(run['cpus'])}"
                f" waited={waited:.0f}s",
                flush=True,
            )
            self.save()
            self.changed.notify_all()

    def leave(self, run):
        """Drop a run whose client left before its start. It finds its place again for a while."""
        with self.changed:
            if run in self.waiting:
                self.waiting.remove(run)
                self.tickets[run["bundle"], run["kind"], run["label"]] = run["queued"]
                self.admit()
                self.changed.notify_all()

    def reap(self):
        """Give back the cores of the runs whose process ended."""
        with self.changed:
            ended = [run for run in self.running if not self.is_alive(run)]
            for run in ended:
                self.running.remove(run)
                ran = time.time() - run["started"]
                after, before = read_cpu_seconds(run["cgroup"]), run.get("cpu_seconds")
                used = f" cpu={(after - before) / ran:.1f}" if after and before and ran > 1 else ""
                print(f"run done {run['bundle']} {run['kind']} ran={ran:.0f}s{used}", flush=True)
            if ended:
                self.save()
            self.admit()
            self.changed.notify_all()

    def watch(self):
        def keep_reaping():
            while True:
                try:
                    self.reap()
                except Exception as error:
                    print(f"run queue: {error!r}", flush=True)
                time.sleep(1)

        threading.Thread(target=keep_reaping, daemon=True).start()

    def get_place(self, run):
        """What a waiting run is told: its place, why it waits and the runs that hold the cores."""
        return {
            "ahead": [
                {"bundle": other["bundle"], "kind": other["kind"], "started": other["started"]}
                for other in self.running
                if self.holds_cores(other)
            ],
            "blocker": run["blocker"],
            "need": run["need"],
            "place": [other for other in self.waiting if other["need"]].index(run) + 1,
        }

    def describe(self):
        with self.changed:
            available, stall = self.read_memory()
            keys = ("bundle", "kind", "label", "need", "queued", "started", "workers")
            return {
                "available": round(available, 1),
                "cores": len(self.cores),
                "cpus": format_cpus(cpu for core in self.cores for cpu in core),
                "free": len(self.get_free_cores()),
                "min_free": self.min_free,
                "queue": [
                    dict({key: run[key] for key in keys}, blocker=run["blocker"])
                    for run in self.waiting
                ],
                "running": [
                    dict(
                        {key: run[key] for key in keys},
                        cpus=format_cpus(run["cpus"]),
                        lent=bool(run["need"]) and not self.holds_cores(run),
                    )
                    for run in self.running
                ],
                "stall": stall,
            }


def check(body):
    kind, workers, label = body.get("kind", "other"), body.get("workers", 1), body.get("label", "")
    if kind not in KINDS:
        raise ValueError(f"kind: one of {', '.join(KINDS)}")
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"workers: an integer from 1 to {MAX_WORKERS}")
    if type(label) is not str:
        raise ValueError("label: a text")
    return kind, workers, " ".join(label.split())[:MAX_LABEL]


def has_left(connection):
    readable, _, _ = select.select([connection], [], [], 0)
    try:
        return bool(readable) and not connection.recv(1, socket.MSG_PEEK)
    except OSError:
        return True


def serve(queue, handler, caller, body):
    """Answer `POST /run` with one JSON line each time the place of the run changes, then one when
    it starts. The run is the peer process, or with `shell` the shell that started it.
    """
    connection = handler.request
    size = struct.calcsize("3i")
    pid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size))[0]
    try:
        kind, workers, label = check(body)
    except ValueError as error:
        data = json.dumps({"error": str(error)}).encode()
        handler.send_response(400)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return
    if body.get("shell"):
        pid = get_parent(pid)
    run = queue.request(caller, pid, kind, workers, label)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-ndjson")
    handler.end_headers()
    sent, told = None, time.monotonic()
    try:
        while True:
            with queue.changed:
                if run["started"] is None:
                    queue.changed.wait(1)
                place = None if run["started"] else queue.get_place(run)
            if place is None:
                break
            if has_left(connection):
                queue.leave(run)
                return
            if place != sent or time.monotonic() - told > KEEPALIVE:
                sent, told = place, time.monotonic()
                handler.wfile.write(json.dumps(place).encode() + b"\n")
                handler.wfile.flush()
        nested = bool(run.get("nested"))
        answer = {
            "cpus": run["cpus"],
            "need": run["need"],
            "nested": nested,
            "waited": 0 if nested else round(run["started"] - run["queued"]),
        }
        handler.wfile.write(json.dumps(answer).encode() + b"\n")
    except OSError:
        queue.leave(run)
