"""The remote workers: where a run goes when a worker can take it, and the relay to it.

A worker is a machine of remote-runs, reached over one SSH connection that `worker-mux@NAME`
holds on the host. This daemon is the gateway of the bundles to it: a bundle asks over its socket,
which names it by the kernel, and the daemon runs a fixed ssh command through that connection
with the bundle's name as its first word. No container gets the connection itself.
See README.md, "Remote runs".
"""

import json
import math
import os
import pathlib
import re
import select
import socket
import subprocess
import threading
import time

MUX = pathlib.Path("~/.local/state/remote-runs/mux").expanduser()
POLL = 15
FRESH = 60
ROUTE_MAX_WAIT = 120
ASK_EVERY = 600
AGENT_GIGABYTES = 1
MAX_ANSWER = 64 * 1024
MAX_COUNT = 10**9
MAX_FOLLOWS = 5
MAX_RELAYS = 4
NUMBERS = ("agents_free", "gigabytes_free", "wait")
STATUS_TIMEOUT = 20
UPLOAD_IDLE = 300
WORD = {
    "commit": re.compile(r"[0-9a-f]{40}"),
    "db": re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}"),
    "job": re.compile(r"[0-9]{1,12}"),
    "repo": re.compile(r"[a-z][a-z0-9-]{0,39}"),
}
BUNDLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}")
STATES = (("running", "running"), ("waiting", "waiting"), ("agent", "agents"))
MAX_AGE = 10**9
LOOKS = {
    "state": ("status", "host"),
    "log": ("look", "host", "log"),
    "memory": ("look", "host", "memory"),
    "disk": ("look", "host", "disk"),
}
MAX_LINES = 2000
LOG_WINDOW = ("since", "until")
EPOCH = re.compile(r"@[0-9]{9,11}")
LOOK_TIMEOUT = 60
VERBS = {
    "status": ((), 0),
    "have": (("repo",), 0),
    "sync": (("repo", "commit"), 2 * 1024**3),
    "put": (("db",), 2 * 1024**3),
    "submit": ((), 64 * 1024),
    "follow": (("job",), 64 * 1024**2),
    "cancel": (("job",), 0),
    "get": (("job",), 0),
    "forget": ((), 0),
}


class Refused(Exception):
    pass


def is_status(status):
    """Whether a worker's status can be used: the fields the choice of a worker counts are
    numbers, its reason to start no agent a short line. One that is not would stop every start
    of the laptop at each pass (security check 11-03, G3)."""
    if type(status) is not dict or "error" in status:
        return False
    for value in (status.get(key, 0) for key in NUMBERS):
        if type(value) is float and not math.isfinite(value):
            return False
        if type(value) not in (int, float) or not -MAX_COUNT < value < MAX_COUNT:
            return False
    wait = status.get("agent_wait")
    return not wait or (type(wait) is str and len(wait) <= 300 and wait.isprintable())


def count_follows(bundle, proc=pathlib.Path("/proc")):
    """The ssh of `follow` that run for a bundle, each one session of the connection: processes
    of the host only, as a bundle names any bundle on a command line of its container."""
    words = [b"worker", b"follow", bundle.encode()]
    try:
        host = os.readlink(proc / "self" / "ns" / "pid")
    except OSError:
        return 0
    count = 0
    for path in proc.glob("[0-9]*/cmdline"):
        try:
            args = path.read_bytes().split(b"\0")
            if os.path.basename(args[0]) != b"ssh" or not any(
                args[index : index + 3] == words for index in range(len(args))
            ):
                continue
            count += os.readlink(path.parent / "ns" / "pid") == host
        except OSError:
            continue
    return count


def is_job(job):
    """Whether a job a worker lists is well formed: its words reach Seb's terminal."""
    return (
        type(job) is dict
        and type(job.get("bundle")) is str
        and bool(BUNDLE.fullmatch(job["bundle"]))
        and type(job.get("kind")) is str
        and bool(WORD["repo"].fullmatch(job["kind"]))
        and type(job.get("age")) is int
        and 0 <= job["age"] < MAX_AGE
    )


class Workers:
    def __init__(self, names, run=subprocess.run, popen=subprocess.Popen):
        self.names = [name for name in names if WORD["repo"].fullmatch(name)]
        self.run = run
        self.popen = popen
        self.status = {}
        self.asked = {}
        self.sent = {}
        self.follows = {}
        self.relays = {}
        self.lock = threading.Lock()

    def socket(self, name):
        return MUX / name / "sock" / "master"

    def is_up(self, name):
        """Connect rather than test the file: a crash of the host leaves the socket file with
        nothing listening."""
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            try:
                client.connect(str(self.socket(name)))
            except OSError:
                return False
        return True

    def ssh(self, name, *words):
        return [
            "ssh", "-F", "none", "-S", str(self.socket(name)), "-o", "ControlMaster=no",
            "-o", "BatchMode=yes", "-T", "worker", *words,
        ]

    def ask(self, name):
        """Have worker-mux open the connection; held while the screen is locked, see here."""
        now = time.time()
        if now - self.asked.get(name, 0) < ASK_EVERY:
            return
        self.asked[name] = now
        request = MUX / name / "request"
        request.mkdir(parents=True, exist_ok=True)
        (request / "start").write_text(f"{now}\n")

    def poll(self):
        for name in self.names:
            if not self.is_up(name):
                continue
            asked = time.time()
            try:
                status = json.loads(self.read_answer(self.ssh(name, "status", "host")))
            except (OSError, ValueError):
                continue
            if is_status(status):
                with self.lock:
                    self.status[name] = (asked, status)

    def read_answer(self, argv):
        """The output of a command, MAX_ANSWER bytes at most, in STATUS_TIMEOUT seconds: ValueError
        past either, as the worker writes it."""
        process = self.popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        data, deadline = b"", time.monotonic() + STATUS_TIMEOUT
        try:
            while len(data) <= MAX_ANSWER:
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([process.stdout], [], [], left)[0]:
                    raise ValueError(f"no whole answer in {STATUS_TIMEOUT} s")
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    return data
                data += chunk
            raise ValueError(f"an answer over {MAX_ANSWER} bytes")
        finally:
            process.kill()
            process.wait()
            process.stdout.close()

    def list_jobs(self):
        """For `bctl runs`: the jobs and agents of the workers that answered lately (worker,
        bundle, kind, state: running, waiting or agent, and since when, as the worker counts it),
        then the workers that did not, with when they last did (0 for never)."""
        jobs, silent, now = [], [], time.time()
        with self.lock:
            statuses = [(name, *self.status.get(name, (0, {}))) for name in self.names]
        for name, seen, status in statuses:
            every = status.get("every")
            if now - seen > FRESH:
                silent.append({"worker": name, "since": seen})
                continue
            for state, group in STATES:
                listed = every.get(group) if type(every) is dict else None
                jobs += [
                    {"worker": name, "state": state, "since": seen - job["age"],
                     "bundle": job["bundle"], "kind": job["kind"]}
                    for job in (listed if type(listed) is list else ())
                    if is_job(job)
                ]
        return jobs, silent

    def look(self, name, what, lines, window=None):
        """For `bctl worker`: a read of a worker, asked as `host` with fixed words, but the line
        count of the log and its window, `since` and `until` as `@EPOCH` (README.md, "Remote
        runs")."""
        window = window or {}
        if name not in self.names:
            raise Refused(f"no worker {name}")
        if what not in LOOKS:
            raise Refused(f"worker: one of {', '.join(LOOKS)}")
        if not 1 <= lines <= MAX_LINES:
            raise Refused(f"worker: 1 to {MAX_LINES} lines")
        if window and what != "log":
            raise Refused("worker: --since and --until go with log only")
        for key in LOG_WINDOW:
            if key in window and not (type(window[key]) is str and EPOCH.fullmatch(window[key])):
                raise Refused(f"worker: --{key} @EPOCH")
        if not self.is_up(name):
            self.ask(name)
            raise Refused(f"no connection to {name}")
        words = [*LOOKS[what], *([str(lines)] if what == "log" else [])]
        words += [word for key in LOG_WINDOW if key in window for word in (key, window[key])]
        try:
            result = self.run(
                self.ssh(name, *words), capture_output=True, timeout=LOOK_TIMEOUT, check=False
            )
            answer = json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            raise Refused(f"{name}: no answer in {LOOK_TIMEOUT} s") from None
        except ValueError:
            raise Refused(f"{name}: not an answer") from None
        if type(answer) is not dict:
            raise Refused(f"{name}: not an answer")
        if "error" in answer:
            error = "".join(char for char in str(answer["error"])[:300] if char.isprintable())
            raise Refused(f"{name}: {error}")
        return answer

    def keep_polling(self):
        while True:
            try:
                self.poll()
            except Exception as error:  # noqa: BLE001
                print(f"workers: {error!r}", flush=True)
            time.sleep(POLL)

    def choose(self, kind, workers):
        """The worker a run of this kind goes to, or None to run it here. Remote first, as the
        laptop is the short one, when a worker starts it within ROUTE_MAX_WAIT seconds.
        """
        now = time.time()
        best = None
        for name in self.names:
            if not self.is_up(name):
                self.ask(name)
                continue
            with self.lock:
                seen, status = self.status.get(name, (0, {}))
            if now - seen > FRESH or status.get("draining"):
                continue
            wait = status.get("wait", ROUTE_MAX_WAIT + 1)
            if wait <= ROUTE_MAX_WAIT and (best is None or wait < best[0]):
                best = (wait, name)
        return best[1] if best else None

    def choose_agent(self, wanted=None):
        """The worker that starts an agent now, or None, the connection asked of each worker with
        none (README.md, "Remote agents")."""
        for name in self.names if wanted is None else [wanted]:
            wait = self.get_agent_wait(name)
            if not wait:
                return name
            if wait[0] == "no link":
                self.ask(name)
        return None

    def get_agent_wait(self, name):
        """(a word, why) the worker starts no agent now, or None: it is up, answered lately, is not
        drained, and has a place and the memory for an agent. Deck shows the word beside it."""
        if name not in self.names:
            return "no worker", f"no worker {name}"
        unreachable = self.get_unreachable(name)
        if unreachable:
            return unreachable
        with self.lock:
            seen, status = self.status.get(name, (0, {}))
            sent = self.sent.get(name, {})
            for bundle in [bundle for bundle, end in sent.items() if end < seen]:
                del sent[bundle]
        gigabytes = status.get("gigabytes_free", 0) - len(sent) * AGENT_GIGABYTES
        if gigabytes < AGENT_GIGABYTES:
            return "full", f"{name} has {gigabytes:.1f} GB free, {AGENT_GIGABYTES} needed"
        if status.get("agent_wait"):
            return "full", f"{name}: {status['agent_wait']}"
        if status.get("agents_free", 0) <= len(sent):
            return "full", f"every agent place of {name} is taken"
        return None

    def send_agent(self, name, bundle):
        """The agent of a bundle on its way to the worker takes a place and its memory there until
        a status asked after its start ended (2026-10-30: 8 agents sent to sebpc in one minute on
        one status, each refused)."""
        with self.lock:
            self.sent.setdefault(name, {})[bundle] = float("inf")

    def end_send(self, name, bundle):
        with self.lock:
            if bundle in self.sent.get(name, {}):
                self.sent[name][bundle] = time.time()

    def is_sending(self, bundle):
        """Whether a start of the bundle's agent on a worker runs in this daemon."""
        with self.lock:
            return any(sent.get(bundle) == float("inf") for sent in self.sent.values())

    def lists_agent(self, name, bundle, after):
        """Whether the worker lists an agent of the bundle in its last answer, None when that
        answer is older than FRESH or was asked before `after`, as a job starts after."""
        with self.lock:
            seen, status = self.status.get(name, (0, {}))
        every = status.get("every")
        agents = every.get("agents") if type(every) is dict else None
        if time.time() - seen > FRESH or seen <= after or type(agents) is not list:
            return None
        return any(is_job(job) and job["bundle"] == bundle for job in agents)

    def get_unreachable(self, name):
        """(a word, why) the worker takes no agent until something changes, or None. A full worker
        frees a place by itself."""
        if not self.is_up(name):
            why = f"no connection to {name}"
            if (MUX / name / "request" / "held").exists():
                why += ", its key is asked at the next unlock of the screen"
            return "no link", why
        with self.lock:
            seen, status = self.status.get(name, (0, {}))
        if time.time() - seen > FRESH:
            since = time.strftime("%H:%M", time.localtime(seen)) if seen else None
            why = f"no answer from {name} since {since}" if since else f"no answer from {name} yet"
            return "no answer", why
        if status.get("leaving"):
            return "leaving", f"{name} is leaving"
        if status.get("draining"):
            return "drained", f"{name} is drained"
        return None

    def is_leaving(self, name):
        """Whether the worker answered lately that it leaves: it shuts down once it holds no
        agent and no job (README.md, "Remote agents")."""
        with self.lock:
            seen, status = self.status.get(name, (0, {}))
        return time.time() - seen <= FRESH and bool(status.get("leaving"))

    def check(self, verb, args):
        if verb not in VERBS:
            raise Refused(f"no worker verb {verb}")
        kinds, _ = VERBS[verb]
        if len(args) != len(kinds):
            raise Refused(f"{verb}: {len(kinds)} words")
        for kind, arg in zip(kinds, args):
            if not WORD[kind].fullmatch(arg):
                raise Refused(f"{verb}: not a valid {kind}")

    def relay(self, handler, bundle, name, verb, args):
        """Pass the bundle's stream to the worker and back, at most the size the verb allows.
        A `follow` sends nothing: its ssh writes to the bundle's connection itself, from a scope
        of its own, so that the job goes on through a restart of the daemon. A bundle follows
        MAX_FOLLOWS jobs at once, the 5 a worker runs or keeps waiting for it, counted here and by
        their ssh, which outlive a restart; the other verbs take MAX_RELAYS sessions of the one
        connection per bundle at most, and an upload that sends nothing for UPLOAD_IDLE seconds
        ends: else one bundle takes every session (security check 11-03, B23).
        """
        self.check(verb, args)
        if name not in self.names:
            raise Refused(f"no worker {name}")
        if not self.is_up(name):
            self.ask(name)
            raise Refused(f"no connection to {name}")
        if verb == "follow":
            with self.lock:
                if max(self.follows.get(bundle, 0), count_follows(bundle)) >= MAX_FOLLOWS:
                    raise Refused(f"{bundle} follows {MAX_FOLLOWS} jobs on the workers already")
                self.follows[bundle] = self.follows.get(bundle, 0) + 1
            try:
                return self.stream(handler, bundle, name, verb, args)
            finally:
                with self.lock:
                    self.follows[bundle] -= 1
                    if not self.follows[bundle]:
                        del self.follows[bundle]
        with self.lock:
            if self.relays.get(bundle, 0) >= MAX_RELAYS:
                raise Refused(f"{bundle} has {MAX_RELAYS} streams to the workers open already")
            self.relays[bundle] = self.relays.get(bundle, 0) + 1
        try:
            handler.connection.settimeout(UPLOAD_IDLE)
            return self.stream(handler, bundle, name, verb, args)
        finally:
            with self.lock:
                self.relays[bundle] -= 1
                if not self.relays[bundle]:
                    del self.relays[bundle]

    def stream(self, handler, bundle, name, verb, args):
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.flush()
        if verb == "follow":
            scope = ["systemd-run", "--user", "--scope", "--collect", "--quiet"]
            argv = [*scope, *self.ssh(name, verb, bundle, *args)]
            return self.popen(argv, stdin=subprocess.DEVNULL, stdout=handler.connection).wait()
        process = self.popen(self.ssh(name, verb, bundle, *args), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE)
        limit = VERBS[verb][1]

        def upload():
            sent = 0
            try:
                while chunk := handler.rfile.read1(65536):
                    sent += len(chunk)
                    if sent > limit:
                        process.kill()
                        break
                    process.stdin.write(chunk)
                    process.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        uploading = threading.Thread(target=upload, daemon=True)
        uploading.start()
        try:
            while chunk := process.stdout.read1(65536):
                handler.wfile.write(chunk)
                handler.wfile.flush()
        except OSError:
            process.kill()
        finally:
            process.stdout.close()
        return process.wait()
