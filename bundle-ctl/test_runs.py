#!/usr/bin/env python3
"""The run queue, without the daemon: what the hook takes for a heavy command, the order runs
start in, and real `bctl run` clients on a socket of their own: python3 test_runs.py
"""

import importlib.util
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon
import runs

ENGINE = "./dist/engine-linux-amd64 --server http://localhost:8471"
ODOO = "/home/seb/virtualenvs/odoo20/bin/python odoo-bin -d x"

KINDS = [
    (f"cd /workspace/odoo && nice -n 19 {ODOO} --test-tags /mail", ("odoo", 1)),
    (f'db="$(git branch --show-current)-claude" && {ODOO} -i mail', ("odoo", 1)),
    ("timeout 590 python3 odoo-bin -d $(testdb) --test-enable --stop-after-init", ("odoo", 1)),
    ("createdb x && ./odoo-bin -d x \\\n  --init=mail > log 2>&1; echo $?", ("odoo", 1)),
    ("ott /mail:TestUi", ("odoo", 1)),
    ("odoo-bin et --stop-after-init -i mail", ("odoo", 1)),
    (f"nohup nice -n 10 {ODOO} --http-port=8137 > s.log 2>&1 &", ("server", 1)),
    ("obet", ("server", 1)),
    ("N=200 nice -n 19 python3 ./odoo-bin shell -d x < probe.py | grep PROBE", ("shell", 1)),
    (f"cd engine && nice -n 19 {ENGINE} --suite-names '@mail' --workers 2 > run", ("hoot", 2)),
    (f"{ENGINE} --test 'a test; with && operators' --flaky-check 5 --workers=1", ("hoot", 1)),
    (f"{ENGINE} --suite-names '@web'", ("hoot", 4)),
    (f"{ENGINE} --debug --workers 3 --test x", ("hoot", 1)),
    ("twc --once -m '@mail'", ("hoot", 4)),
    ("node /home/seb/repo/TestWarden/release/test-warden.cjs --once --workers 3", ("hoot", 3)),
    (f"for i in 1 2 3; do {ENGINE} --test x --workers 1; done", ("hoot", 1)),
    (f"{ODOO} & sleep 20; {ENGINE} --workers 2; kill %1", ("hoot", 2)),
    (f"bash -c '{ODOO} --test-tags /mail'", ("odoo", 1)),
    (f'S=/tmp/x; E="nice -n 19 {ENGINE} --workers 2"; $E --test x > $S/a', ("hoot", 2)),
    ("S={folder}; bash $S/battery.sh 8331 tag", ("hoot", 3)),
    ("cd {folder} && ./battery.sh", ("hoot", 3)),
    ("bash {folder}/missing.sh", None),
    ("python3 ./odoo-bin -d \"it's --test-tags /mail", ("odoo", 1)),
    ("pkill -f odoo-bin; ps aux | grep odoo-bin; pgrep -af 'odoo-bin|engine-linux-amd64'", None),
    ("kill $(pgrep -f odoo-bin)", None),
    ("python3 odoo-bin --help | grep -E 'with-demo'", None),
    ("./odoo-bin scaffold my_module addons", None),
    ("git log --oneline -3 -- odoo-bin && cat odoo-bin | head", None),
    ("echo 'then run ./odoo-bin -d x --test-tags /mail'", None),
    ("python3 - <<'EOF'\nimport json\nprint(json.dumps({'odoo-bin': 1}))\nEOF", None),
    (f"{ENGINE} --help", None),
    ("which twc; type ott", None),
]


def load_hook():
    spec = importlib.util.spec_from_file_location("run_hook", HERE / "client" / "run-hook.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_kinds(failures):
    hook = load_hook()
    with tempfile.TemporaryDirectory() as folder:
        script = pathlib.Path(folder) / "battery.sh"
        script.write_text(f'#!/bin/bash\nE="{ENGINE} --workers 3"\n$E --suite-names "$2"\n')
        for text, expected in KINDS:
            found = hook.classify(text.replace("{folder}", folder), folder)
            if (found and found[:2]) != expected:
                failures.append(f"kind of {text!r}: {found}, expected {expected}")
    return len(KINDS)


class Host:
    """A queue on 4 cores of 2 CPUs, with processes and a memory the test sets."""

    def __init__(self, path=None):
        self.available = 20.0
        self.births = {}
        self.stall = 0.0
        cores = [(0, 1), (2, 3), (4, 5), (6, 7)]
        self.queue = runs.RunQueue(cores, 4, path, self.read_memory, self.births.get)

    def read_memory(self):
        return self.available, self.stall

    def ask(self, pid, kind, workers=1, bundle=None):
        self.births[pid] = pid
        return self.queue.request(bundle or f"bundle-{pid}", pid, kind, workers, f"label-{pid}")

    def end(self, *pids):
        for pid in pids:
            del self.births[pid]
        self.queue.reap()

    def state(self):
        return (
            {run["pid"]: runs.format_cpus(run["cpus"]) for run in self.queue.running},
            {run["pid"]: run["blocker"] for run in self.queue.waiting},
        )


def order_by_cores():
    host = Host()
    host.ask(1, "hoot", 2)
    host.ask(2, "odoo")
    host.ask(3, "odoo")
    host.ask(4, "hoot", 4)
    host.ask(5, "odoo")
    host.ask(6, "server")
    before = host.state()
    host.end(2)
    middle = host.state()
    host.end(1)
    return before, middle, host.state()


def order_by_memory():
    host = Host()
    host.available = 3.0
    host.ask(1, "odoo")
    host.ask(2, "odoo")
    before = host.state()
    host.available = 7.0
    host.queue.reap()
    ramping = host.state()
    host.queue.running[0]["started"] -= runs.RAMP
    host.queue.reap()
    host.stall = 20.0
    host.ask(3, "odoo")
    return before, ramping, host.state()


def keep_place():
    host = Host()
    host.ask(1, "hoot", 2)
    host.ask(2, "hoot", 2)
    left = host.ask(3, "odoo")
    host.ask(4, "odoo")
    host.queue.leave(left)
    host.ask(5, "odoo")
    host.ask(3, "odoo")
    return [run["pid"] for run in host.queue.waiting]


def lend_after_an_hour():
    host = Host()
    host.ask(1, "hoot", 2)
    host.ask(2, "hoot", 2)
    host.ask(3, "odoo")
    before = host.state()
    host.queue.running[0]["started"] -= runs.HOLD
    host.queue.reap()
    return before, host.state(), [run["lent"] for run in host.queue.describe()["running"]]


def share_cores_of_parent():
    queue = runs.RunQueue([(0, 1), (2, 3)], 4, None, lambda: (20.0, 0.0))
    child = subprocess.Popen(["sleep", "5"])
    try:
        queue.request("bundle", os.getpid(), "hoot", 2, "outer")
        inner = queue.request("bundle", child.pid, "hoot", 2, "inner")
        return inner.get("nested"), inner["cpus"], len(queue.running), len(queue.waiting)
    finally:
        child.kill()
        child.wait()


def adopt_after_restart():
    with tempfile.TemporaryDirectory() as folder:
        path = pathlib.Path(folder) / "runs.json"
        host = Host(path)
        host.ask(1, "odoo")
        host.ask(2, "odoo")
        again = Host(path)
        again.births[2] = 2
        again.queue.load()
        return [run["pid"] for run in again.queue.running]


QUEUE_CASES = [
    (
        order_by_cores,
        (
            ({1: "0-3", 2: "4-5", 3: "6-7", 6: "0-7"}, {4: "cores", 5: "queue"}),
            ({1: "0-3", 3: "6-7", 6: "0-7"}, {4: "cores", 5: "queue"}),
            ({3: "6-7", 6: "0-7", 4: "0-3", 5: "4-5"}, {}),
        ),
    ),
    (
        order_by_memory,
        (
            ({1: "0-1"}, {2: "memory"}),
            ({1: "0-1"}, {2: "memory"}),
            ({1: "0-1", 2: "2-3"}, {3: "memory"}),
        ),
    ),
    (keep_place, [3, 4, 5]),
    (
        lend_after_an_hour,
        (
            ({1: "0-3", 2: "4-7"}, {3: "cores"}),
            ({1: "0-3", 2: "4-7", 3: "0-1"}, {}),
            [True, False, False],
        ),
    ),
    (share_cores_of_parent, (True, [0, 1, 2, 3], 1, 0)),
    (adopt_after_restart, [2]),
]


def check_queue(failures):
    for case, expected in QUEUE_CASES:
        found = case()
        if found != expected:
            failures.append(f"{case.__name__}: {found}, expected {expected}")
    return len(QUEUE_CASES)


def serve(queue, path):
    """The daemon's own server on this queue, every caller taken for the bundle `bundle`."""
    daemon.RUNS = queue
    daemon.identify = lambda connection: "bundle"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def start_client(path, *args, shell=None):
    """`bctl ARGS` on the socket at `path`, or with `shell` that text run after `bctl ARGS`."""
    code = (
        "import pathlib, sys; sys.path.insert(0, sys.argv[1]); import bctl;"
        " bctl.SOCKETS = (pathlib.Path(sys.argv[2]),); bctl.RUN_RETRY = 0;"
        " sys.argv[1:] = sys.argv[3:]; bctl.main()"
    )
    command = [sys.executable, "-c", code, str(HERE / "client"), str(path), *args]
    if shell:
        text = " ".join(f"'{word}'" for word in command)
        command = ["bash", "-c", f"{text}\n{shell}"]
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait_until(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def check_clients(failures):
    """Real clients: one holds every core, the next waits for it, a server does not, a shell is
    pinned as a command is, a client that dies leaves the queue, and no socket runs the command.
    """
    cpus = sorted(os.sched_getaffinity(0))
    if len(cpus) < 4:
        print("under 4 CPUs here: the clients are not tested")
        return 0
    cores = [tuple(cpus[:2]), tuple(cpus[2:4])]
    show = "import os; print(sorted(os.sched_getaffinity(0)))"
    checks = []
    with tempfile.TemporaryDirectory() as folder:
        path = pathlib.Path(folder) / "ctl.sock"
        queue = runs.RunQueue(cores, 0, None, lambda: (20.0, 0.0))
        queue.watch()
        server = serve(queue, path)
        holder = start_client(
            path, "run", "--kind", "hoot", "--workers", "2", "--", sys.executable, "-c",
            f"{show}; import time; time.sleep(3)",
        )
        checks.append(("the first run starts", wait_until(lambda: len(queue.running) == 1)))
        started = time.monotonic()
        waiter = start_client(path, "run", "--kind", "odoo", "--", sys.executable, "-c", show)
        server_run = start_client(path, "run", "--kind", "server", "--", sys.executable, "-c", show)
        dying = start_client(path, "run", "--kind", "odoo", "--", "true")
        checks.append(("two runs wait", wait_until(lambda: len(queue.waiting) == 2)))
        dying.send_signal(signal.SIGKILL)
        checks.append(("a killed client leaves", wait_until(lambda: len(queue.waiting) == 1)))
        listed = start_client(path, "runs").communicate(timeout=10)[0].splitlines()
        checks.append(
            (
                "bctl runs lists them",
                listed[0].split()[:3] == ["running", "bundle", "hoot"]
                and any(line.split()[:4] == ["queued", "1", "bundle", "odoo"] for line in listed)
                and listed[-1].startswith("2/2 cores taken"),
            ),
        )
        shared = server_run.communicate(timeout=10)[0].strip()
        checks.append(("a server shares the pool at once", shared == str(cpus[:4])))
        held, _ = holder.communicate(timeout=10)
        checks.append(("the first run is pinned to both cores", held.strip() == str(cpus[:4])))
        out, err = waiter.communicate(timeout=10)
        waited = time.monotonic() - started
        checks.append(("the next run waits for it", waited > 2 and "waits for 1 core" in err))
        checks.append(("and names what runs", "Running: bundle (hoot" in err))
        checks.append(("then takes the first core", out.strip() == str(cpus[:2])))
        checks.append(("and frees it when it ends", wait_until(lambda: not queue.running)))
        shell = start_client(
            path, "run", "--kind", "odoo", shell=f"{sys.executable} -c '{show}'; sleep 1",
        )
        checks.append(("a shell holds a core", wait_until(lambda: len(queue.running) == 1)))
        out, _ = shell.communicate(timeout=10)
        checks.append(("its next command is pinned", out.strip() == str(cpus[:2])))
        checks.append(("and it frees the core", wait_until(lambda: not queue.running)))
        server.shutdown()
        server.server_close()
        path.unlink()
        alone = start_client(path, "run", "--", "sh", "-c", "echo ran; exit 3")
        out, err = alone.communicate(timeout=10)
        checks.append(
            (
                "no socket: the command runs",
                (out.strip(), alone.returncode, "no run queue" in err) == ("ran", 3, True),
            ),
        )
    failures.extend(f"clients: {name}" for name, passed in checks if not passed)
    return len(checks)


def main():
    failures = []
    count = check_kinds(failures) + check_queue(failures) + check_clients(failures)
    for line in failures:
        print(line)
    print(f"{count - len(failures)}/{count} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
