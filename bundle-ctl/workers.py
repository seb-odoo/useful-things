"""The remote workers: where a run goes when a worker can take it, and the relay to it.

A worker is a machine of remote-runs, reached over one SSH connection that `worker-mux@NAME`
holds on the host. This daemon is the gateway of the bundles to it: a bundle asks over its socket,
which names it by the kernel, and the daemon runs a fixed ssh command through that connection
with the bundle's name as its first word. No container gets the connection itself.
See README.md, "Remote runs".
"""

import json
import pathlib
import re
import subprocess
import threading
import time

MUX = pathlib.Path("~/.local/state/remote-runs/mux").expanduser()
POLL = 15
FRESH = 60
ROUTE_MAX_WAIT = 120
ASK_EVERY = 600
AGENT_GIGABYTES = 1
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
        self.lock = threading.Lock()

    def socket(self, name):
        return MUX / name / "sock" / "master"

    def is_up(self, name):
        return self.socket(name).is_socket()

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
                result = self.run(self.ssh(name, "status", "host"), capture_output=True,
                                  timeout=20, check=False)
                status = json.loads(result.stdout)
            except (subprocess.TimeoutExpired, ValueError):
                continue
            if type(status) is dict and "error" not in status:
                with self.lock:
                    self.status[name] = (asked, status)

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

    def look(self, name, what, lines):
        """For `bctl worker`: a read of a worker, asked as `host` with fixed words, but the line
        count of the log (README.md, "Remote runs")."""
        if name not in self.names:
            raise Refused(f"no worker {name}")
        if what not in LOOKS:
            raise Refused(f"worker: one of {', '.join(LOOKS)}")
        if not 1 <= lines <= MAX_LINES:
            raise Refused(f"worker: 1 to {MAX_LINES} lines")
        if not self.is_up(name):
            self.ask(name)
            raise Refused(f"no connection to {name}")
        words = [*LOOKS[what], *([str(lines)] if what == "log" else [])]
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
            except OSError as error:
                print(f"workers: {error}", flush=True)
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
        of its own, so that the job goes on through a restart of the daemon.
        """
        self.check(verb, args)
        if name not in self.names:
            raise Refused(f"no worker {name}")
        if not self.is_up(name):
            self.ask(name)
            raise Refused(f"no connection to {name}")
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
