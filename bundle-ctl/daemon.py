#!/usr/bin/env python3
"""Answer the bundle requests of the dev containers, on a unix socket every bundle container mounts.

    systemctl --user start bundle-ctl
    curl --unix-socket ~/.local/state/bundle-ctl/sock/ctl.sock http://bundle-ctl/whoami

A container reaches neither the host network nor podman, so this is its one way to the host, with
fixed verbs. The caller is the bundle whose container runs the connecting process, read from the
kernel and never from the request.
"""

from concurrent.futures import ThreadPoolExecutor
import http.server
import importlib.util
import json
import os
import pathlib
import re
import secrets
import shlex
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agents import (  # noqa: E402
    LAUNCH_GRACE,
    STATE,
    STATE_FILE,
    format_time,
    get_agent,
    get_agent_folder,
    get_bundle_container,
    get_mtime,
    has_window,
    read_state,
    read_text,
)
from branch_status import (  # noqa: E402
    PR_URL,
    get_local_branches,
    get_open_bundle_folders,
    get_pr,
    get_status,
    has_write_tree,
)
from commands import (  # noqa: E402
    MADE,
    get_base_from_bundle_name,
    get_bundle_name_from_base_and_name,
    get_remote_dev_ref,
    get_repo_folder,
    get_repos,
    get_sticky_bundles,
    get_worktree_bundle_folder,
    get_worktree_container_folder,
)
from config import STICKY_BUNDLES  # noqa: E402
from utils import UtilsRunner  # noqa: E402


def load_config():
    """devcontainer/config.py, under another name than the scripts' own config module."""
    path = HERE.parent / "devcontainer" / "config.py"
    spec = importlib.util.spec_from_file_location("odoo_dev_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load()


CONFIG = load_config()
AGENT_TERMINAL = CONFIG.get("AGENT_TERMINAL") or "gnome-terminal --window --title {bundle} --"
BRANCHES_TIMEOUT = 120
BUNDLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
CONTAINER_ID = re.compile(r"/libpod-(?:payload-)?([0-9a-f]{64})")
GITHUB_OWNER = re.compile(r"[A-Za-z0-9-]{1,39}")
HOST = "host"
HOST_FOLDER = CONFIG.get("BUNDLE_CTL_HOST_FOLDER")
JOB_WAIT = 50
MAX_AGENTS = int(
    os.environ.get("BUNDLE_CTL_MAX_AGENTS") or CONFIG.get("BUNDLE_CTL_MAX_AGENTS") or 8,
)
MAX_BODY = 64 * 1024
MAX_PRIORITY = 100
MAX_RELAUNCHES = 3
MAX_TASK = 32 * 1024
MAX_WIDTH = 400
NAME = re.compile(r"[a-z0-9](?:[a-z0-9.]|-(?!-)){0,79}")
SESSION_VARIABLES = (
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "SSH_AUTH_SOCK",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
)
SOCKET = STATE / "sock" / "ctl.sock"

folder_by_container = {}
jobs = {}
state_lock = threading.Lock()
work_lock = threading.Lock()


class Refused(Exception):
    """A request that is valid but cannot run now, answered with a 409."""


def get_container_folder(container_id):
    if container_id not in folder_by_container:
        res = subprocess.run(
            [
                "podman",
                "inspect",
                "--format",
                '{{index .Config.Labels "devcontainer.local_folder"}}',
                container_id,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        if res.returncode:
            return ""
        folder_by_container[container_id] = res.stdout.strip()
    return folder_by_container[container_id]


def identify(connection):
    """The bundle of the container running the peer process, HOST, or None when refused."""
    pid, _uid, _gid = struct.unpack(
        "3i",
        connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")),
    )
    try:
        cgroup = pathlib.Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return None
    if "libpod" not in cgroup:
        return HOST
    match = CONTAINER_ID.search(cgroup)
    folder = match and get_container_folder(match[1])
    if folder and folder == HOST_FOLDER:
        return HOST
    bundle = os.path.basename(folder or "")
    if not bundle or folder != get_worktree_bundle_folder(bundle):
        return None
    return bundle


def whoami(caller, query, body):
    if caller == HOST:
        return 200, {"caller": HOST}
    return 200, {
        "base": get_base_from_bundle_name(caller),
        "bundle": caller,
        "caller": caller,
        "parent": read_text(get_agent_folder(caller) / "parent"),
    }


def branches(caller, query, body):
    command = [sys.executable, str(SCRIPTS / "branch_status.py")]
    env = dict(os.environ)
    table = query.get("format") == ["table"]
    if table:
        width = min(max(int(query.get("width", [MAX_WIDTH])[0]), 40), MAX_WIDTH)
        env.pop("NO_COLOR", None)
        env.update(COLUMNS=str(width), FORCE_COLOR="1", TTY_INTERACTIVE="0")
    else:
        command.append("--json")
    try:
        res = subprocess.run(
            command,
            capture_output=True,
            check=False,
            cwd=SCRIPTS,
            env=env,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=BRANCHES_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise Refused(f"branch_status.py took over {BRANCHES_TIMEOUT}s") from None
    if res.returncode:
        raise Refused(f"branch_status.py exited with {res.returncode}: {res.stderr[-500:].strip()}")
    return 200, {"table": res.stdout} if table else json.loads(res.stdout)


def status(caller, query, body):
    repos = [repo for repo in get_repos() if os.path.isdir(get_repo_folder(repo))]
    write_tree = has_write_tree()
    repos_by_bundle = {}
    dates = {}
    with ThreadPoolExecutor() as executor:
        for repo, branches in zip(repos, executor.map(get_local_branches, repos)):
            for branch, date, _head, _in_worktree in branches:
                if branch not in get_sticky_bundles(repo):
                    repos_by_bundle.setdefault(branch, []).append(repo)
                    reflog = f"{get_repo_folder(repo)}/.git/logs/{get_remote_dev_ref(branch, repo)}"
                    pushed = get_mtime(pathlib.Path(reflog)) or 0
                    dates[branch] = max(dates.get(branch, 0), date, pushed)
        pairs = [(repo, bundle) for bundle, names in repos_by_bundle.items() for repo in names]
        open_future = executor.submit(get_open_bundle_folders)
        statuses = dict(
            zip(pairs, executor.map(lambda pair: get_status(*pair, write_tree), pairs)),
        )
        open_folders = open_future.result()
    with state_lock:
        state = read_state()
    now = time.time()
    rows = []
    for bundle, names in sorted(repos_by_bundle.items()):
        folder = get_worktree_bundle_folder(bundle)
        is_open = folder in open_folders
        opening = now - state["launches"].get(bundle, 0) < LAUNCH_GRACE
        agent = get_agent(bundle, dates[bundle], is_open or opening)
        if agent and agent["state"] == "stopped":
            agent["task"] = read_text(get_agent_folder(bundle) / "task.md")
        rows.append(
            {
                "agent": agent,
                "base": get_base_from_bundle_name(bundle),
                "bundle": bundle,
                "folder": os.path.isdir(folder),
                "open": is_open,
                "opening": opening,
                "repos": {repo: statuses[repo, bundle] for repo in names},
            },
        )
    return 200, {
        "agents": count_agents(get_open_windows()),
        "bundles": rows,
        "max_agents": MAX_AGENTS,
        "queue": [
            dict(
                bundle=item["bundle"],
                parent=item["parent"],
                priority=item.get("priority", 0),
                queued=item.get("queued") and format_time(item["queued"]),
            )
            for item in state["queue"]
        ],
    }


def session_env():
    """The daemon's environment plus the graphical session and SSH agent of the logged-in user.

    A unit started at boot has neither: they reach the user manager only once the desktop session
    imports them, so they are read again for each job.
    """
    env = dict(os.environ, COLUMNS="160", NO_COLOR="1", TERM="dumb")
    out = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        capture_output=True,
        check=False,
        text=True,
    ).stdout
    for line in out.splitlines():
        key, _, value = line.partition("=")
        if key in SESSION_VARIABLES:
            env[key] = value
    return env


def run_script(log, script, *args):
    log.write(f"$ {script} {' '.join(args)}\n")
    log.flush()
    res = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=SCRIPTS,
        env=session_env(),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if res.returncode:
        raise Refused(f"{script} exited with {res.returncode}")


def run_in_session(log, *command, scope=True):
    """Run a command of the graphical session in a unit of its own.

    Started here, it would die with the daemon's cgroup. A scope waits for the command to end, a
    service does not.
    """
    env = session_env()
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        raise Refused("no graphical session to open a window in")
    session = [f"--setenv={key}={env[key]}" for key in SESSION_VARIABLES if key in env]
    unit = ["--scope"] if scope else session
    res = subprocess.run(
        ["systemd-run", "--user", "--collect", "--quiet", *unit, *command],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=60,
    )
    if res.returncode:
        raise Refused(f"{command[0]} exited with {res.returncode}")


def open_window(log, bundle):
    log.write(f"opening {bundle}\n")
    log.flush()
    folder = get_worktree_bundle_folder(bundle)
    run_in_session(log, "code", "--folder-uri", UtilsRunner()._devcontainer_folder_uri(folder))


def open_terminal(log, bundle):
    log.write(f"starting the agent of {bundle} in a terminal\n")
    log.flush()
    terminal = shlex.split(AGENT_TERMINAL.format(bundle=bundle))
    agent = [sys.executable, str(HERE / "agent-terminal.py"), bundle]
    run_in_session(log, *terminal, *agent, scope=False)


def run_job(job, work):
    with work_lock, open(job["log"], "a", buffering=1) as log:
        job["state"] = "running"
        try:
            job["result"] = work(log) or {}
            job["state"] = "done"
        except Refused as error:
            log.write(f"{error}\n")
            job["result"] = {"error": str(error)}
            job["state"] = "failed"
        except Exception as error:
            log.write(traceback.format_exc())
            job["result"] = {"error": repr(error)}
            job["state"] = "failed"
    print(f"{job['caller']} job {job['id']} {job['verb']} {job['state']}", flush=True)


def start_job(caller, verb, work):
    (STATE / "jobs").mkdir(parents=True, exist_ok=True)
    job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    job = {
        "caller": caller,
        "id": job_id,
        "log": str(STATE / "jobs" / f"{job_id}.log"),
        "result": None,
        "state": "waiting",
        "verb": verb,
    }
    jobs[job_id] = job
    threading.Thread(target=run_job, args=(job, work), daemon=True).start()
    return 202, {"job": job_id}


def get_job(caller, query, body):
    job = jobs.get(query.get("id", [""])[0])
    if not job:
        raise ValueError("no such job")
    offset = int(query.get("offset", ["0"])[0])
    deadline = time.monotonic() + min(int(query.get("wait", ["0"])[0]), JOB_WAIT)
    log = pathlib.Path(job["log"])
    while job["state"] in ("waiting", "running") and time.monotonic() < deadline:
        if log.exists() and log.stat().st_size > offset:
            break
        time.sleep(0.5)
    data = log.read_bytes()[offset:] if log.exists() else b""
    return 200, dict(job, log=data.decode(errors="replace"), offset=offset + len(data))


def write_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE_FILE)


def get_open_windows():
    root = f"{get_worktree_container_folder()}/"
    return {
        folder
        for folder in get_open_bundle_folders()
        if folder.startswith(root) and os.path.isdir(folder)
    }


def count_agents(folders):
    return sum(os.path.exists(f"{folder}/.agent/terminal") for folder in folders)


def check_task(bundle, body):
    if "task" not in body:
        if "priority" in body:
            raise ValueError("priority: only with a task")
        if body.get("resume"):
            raise ValueError("resume: only with a task")
        return
    task, priority = body["task"], body.get("priority", 0)
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK:
        raise ValueError(f"task: a text of at most {MAX_TASK} characters")
    if type(priority) is not int or abs(priority) > MAX_PRIORITY:
        raise ValueError(f"priority: an integer from -{MAX_PRIORITY} to {MAX_PRIORITY}")
    with state_lock:
        state = read_state()
    alive = (
        get_worktree_bundle_folder(bundle) in get_open_bundle_folders()
        or time.time() - state["launches"].get(bundle, 0) < LAUNCH_GRACE
    )
    agent = get_agent(bundle, alive=alive)
    if agent and agent["state"] not in ("done", "stopped"):
        raise Refused(f"an agent already works in {bundle}")
    if any(item["bundle"] == bundle for item in state["queue"]):
        raise Refused(f"{bundle} is already queued")
    if body.get("resume"):
        if not (get_agent_folder(bundle) / "session").exists():
            raise Refused(f"{bundle} has no session to resume")
        container = get_bundle_container(bundle)
        if container and has_window(container):
            raise Refused(f"{bundle} is open in a window: its session goes on in its Claude tab")


def enqueue(caller, bundle, body):
    priority = body.get("priority", 0)
    with state_lock:
        state = read_state()
        queue = state["queue"]
        position = next(
            (index for index, item in enumerate(queue) if item.get("priority", 0) < priority),
            len(queue),
        )
        queue.insert(
            position,
            {
                "bundle": bundle,
                "parent": caller,
                "priority": priority,
                "queued": time.time(),
                "resume": bool(body.get("resume")),
                "task": body["task"],
            },
        )
        state.get("relaunches", {}).pop(bundle, None)
        write_state(state)
    launch_queued()
    with state_lock:
        queue = [item["bundle"] for item in read_state()["queue"]]
    if bundle in queue:
        return {"bundle": bundle, "priority": priority, "queued": queue.index(bundle) + 1}
    return {"bundle": bundle, "priority": priority, "started": True}


def write_task(item):
    folder = get_agent_folder(item["bundle"])
    if folder.exists() and (
        (folder / "session").exists() or read_text(folder / "task.md") != item["task"].strip()
    ):
        history = folder / "history" / time.strftime("%Y%m%d-%H%M%S")
        history.mkdir(parents=True)
        kept = {"history", "session"} if item.get("resume") else {"history"}
        for path in folder.iterdir():
            if path.name not in kept:
                path.rename(history / path.name)
    folder.mkdir(exist_ok=True)
    (folder / "parent").write_text(f"{item['parent']}\n")
    if "queued" in item:
        (folder / "queued").write_text(f"{item['queued']}\n")
    (folder / "task.md").write_text(item["task"])


def launch_queued():
    with state_lock:
        state = read_state()
        now = time.time()
        open_windows = get_open_windows()
        # Count a window from its launch, as its container shows in podman only once it is built.
        # Free the slot of a launch agent-terminal.py marked `done`: no container could start.
        state["launches"] = {
            bundle: launched
            for bundle, launched in state["launches"].items()
            if now - launched < LAUNCH_GRACE
            and get_worktree_bundle_folder(bundle) not in open_windows
            and (get_mtime(get_agent_folder(bundle) / "done") or 0) < launched
        }
        # VS Code drops a launch at logout or when it restores its windows: take the task again.
        queued = {item["bundle"] for item in state["queue"]}
        relaunches = state.setdefault("relaunches", {})
        stranded = []
        root = pathlib.Path(get_worktree_container_folder())
        for task in sorted(root.glob("*/*/.agent/task.md")):
            bundle = task.parent.parent.name
            if (task.parent / "session").exists():
                relaunches.pop(bundle, None)
            elif (
                str(task.parent.parent) not in open_windows
                and bundle not in state["launches"]
                and bundle not in queued
                and relaunches.get(bundle, 0) < MAX_RELAUNCHES
            ):
                relaunches[bundle] = relaunches.get(bundle, 0) + 1
                parent = read_text(task.parent / "parent")
                stranded.append(
                    {
                        "bundle": bundle,
                        "parent": parent,
                        "queued": float(read_text(task.parent / "queued") or now),
                        "task": task.read_text(),
                    },
                )
        state["queue"][:0] = stranded
        env = session_env()
        has_display = env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")
        queue = []
        for item in state["queue"]:
            bundle = item["bundle"]
            is_running = get_worktree_bundle_folder(bundle) in open_windows
            container = is_running and get_bundle_container(bundle)
            has_room = count_agents(open_windows) + len(state["launches"]) < MAX_AGENTS
            if container and has_window(container):
                # The claude-autoopen extension of the window starts it.
                write_task(item)
            elif container and (get_agent_folder(bundle) / "terminal").exists():
                # Wait for its last terminal to close: the `died` event comes back here.
                queue.append(item)
            elif has_display and (is_running or has_room):
                write_task(item)
                if not is_running:
                    state["launches"][bundle] = now
                start_job(
                    item["parent"],
                    "spawn",
                    lambda log, bundle=bundle: open_terminal(log, bundle),
                )
            else:
                queue.append(item)
        state["queue"] = queue
        write_state(state)


def log_addresses(*containers):
    """Print the bundle or the container behind each address, as a gateway log shows only that."""
    inspect = subprocess.run(
        ["podman", "inspect", *containers],
        capture_output=True,
        check=False,
        text=True,
    )
    for container in json.loads(inspect.stdout or "[]"):
        folder = (container["Config"].get("Labels") or {}).get("devcontainer.local_folder")
        name = os.path.basename(folder) if folder else container["Name"]
        for network in (container["NetworkSettings"].get("Networks") or {}).values():
            if network.get("IPAddress"):
                print(f"address {network['IPAddress']} {name}", flush=True)


def keep_launching():
    def on_events():
        running = subprocess.run(
            ["podman", "ps", "-q"],
            capture_output=True,
            check=False,
            text=True,
        ).stdout.split()
        if running:
            log_addresses(*running)
        while True:
            proc = subprocess.Popen(
                [
                    "podman",
                    "events",
                    "--filter",
                    "type=container",
                    "--filter",
                    "event=start",
                    "--filter",
                    "event=died",
                    "--format",
                    "{{.ID}} {{.Status}}",
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            for line in proc.stdout:
                container, _, status = line.strip().partition(" ")
                if status == "start":
                    log_addresses(container)
                launch_safely()
            proc.wait()
            time.sleep(5)

    def every_minute():
        while True:
            launch_safely()
            time.sleep(60)

    threading.Thread(target=on_events, daemon=True).start()
    threading.Thread(target=every_minute, daemon=True).start()


def launch_safely():
    try:
        launch_queued()
    except Exception:
        traceback.print_exc()


def local_branches(bundle):
    return [
        repo
        for repo in get_repos()
        if subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{bundle}"],
            capture_output=True,
            check=False,
            cwd=get_repo_folder(repo),
        ).returncode
        == 0
    ]


def is_made(folder):
    """Whether create or fetch reached its end, where they leave this file (a link, before)."""
    return os.path.exists(f"{folder}/{MADE}") or os.path.islink(f"{folder}/.devcontainer")


def check_unpushed(bundle, script):
    for repo in local_branches(bundle):
        unpushed = subprocess.run(
            ["git", "log", "--oneline", f"refs/heads/{bundle}", "--not", "--remotes"],
            capture_output=True,
            check=False,
            cwd=get_repo_folder(repo),
            text=True,
        ).stdout.strip()
        if unpushed:
            raise Refused(f"{bundle} has unpushed commits in {repo}, {script} would reset them")


def create(caller, query, body):
    base, name = body.get("base"), body.get("name") or ""
    if base not in STICKY_BUNDLES:
        raise ValueError(f"base must be one of {', '.join(STICKY_BUNDLES)}")
    if not NAME.fullmatch(name):
        raise ValueError("name: lowercase letters, digits, dots and single dashes")
    bundle = get_bundle_name_from_base_and_name(base, name)
    if get_base_from_bundle_name(bundle) != base:
        raise ValueError(f"{bundle} would be read as a bundle of another base")
    branch_repos = body.get("branch_repos") or ["odoo"]
    if unknown := set(branch_repos) - set(get_repos()):
        raise ValueError(f"unknown repos: {', '.join(sorted(unknown))}")
    folder = get_worktree_bundle_folder(bundle)
    if is_made(folder) or (not os.path.exists(folder) and local_branches(bundle)):
        raise Refused(f"{bundle} already exists")
    check_unpushed(bundle, "gnb")
    check_task(bundle, body)

    def work(log):
        args = [base, name, "--no-push", "--no-open"]
        for repo in branch_repos:
            args += ["--branch-repo", repo]
        return build(log, caller, bundle, body, "create_bundle.py", *args)

    return start_job(caller, "create", work)


def fetch(caller, query, body):
    name = body.get("name") or ""
    if pr_match := PR_URL.match(name):
        name = pr_match[0]
        pr = get_pr(*pr_match.groups(), "baseRefName,headRefName")
        bundle, base = pr["headRefName"], pr["baseRefName"]
    else:
        owner, _, bundle = name.rpartition(":")
        if owner and not GITHUB_OWNER.fullmatch(owner):
            raise ValueError(f"not a runbot label: {name}")
        base = get_base_from_bundle_name(bundle)
    if not BUNDLE_NAME.fullmatch(bundle) or base not in STICKY_BUNDLES:
        raise ValueError(f"not a bundle name: {bundle}")
    if is_made(get_worktree_bundle_folder(bundle)):
        raise Refused(f"{bundle} is already here, open it instead")
    check_unpushed(bundle, "pfb")
    check_task(bundle, body)

    def work(log):
        return build(log, caller, bundle, body, "fetch_bundle.py", name, "--no-open")

    return start_job(caller, "fetch", work)


def open_bundle(caller, query, body):
    bundle = body.get("bundle") or ""
    if not BUNDLE_NAME.fullmatch(bundle) or not os.path.isdir(get_worktree_bundle_folder(bundle)):
        raise ValueError(f"no bundle folder for {bundle}")
    if not is_made(get_worktree_bundle_folder(bundle)):
        raise Refused(f"{bundle} is half-made (its create or fetch failed): do it again")
    check_task(bundle, body)
    if "task" in body:
        return 202, enqueue(caller, bundle, body)
    return start_job(caller, "open", lambda log: open_or_enqueue(log, caller, bundle, body))


def set_building(bundle, building):
    with state_lock:
        state = read_state()
        others = [name for name in state.get("building", []) if name != bundle]
        state["building"] = others + [bundle] * building
        write_state(state)


def build(log, caller, bundle, body, script, *args):
    for_agent = "task" in body
    if for_agent:
        set_building(bundle, True)
    try:
        run_script(log, script, *args)
        return open_or_enqueue(log, caller, bundle, body)
    finally:
        if for_agent:
            set_building(bundle, False)


def open_or_enqueue(log, caller, bundle, body):
    if "task" in body:
        return enqueue(caller, bundle, body)
    if body.get("open", True):
        open_window(log, bundle)
        with state_lock:
            state = read_state()
            state["launches"][bundle] = time.time()
            write_state(state)
    return {"bundle": bundle}


VERBS = {
    ("GET", "/branches"): branches,
    ("GET", "/job"): get_job,
    ("GET", "/status"): status,
    ("GET", "/whoami"): whoami,
    ("POST", "/create"): create,
    ("POST", "/fetch"): fetch,
    ("POST", "/open"): open_bundle,
}


class Handler(http.server.BaseHTTPRequestHandler):
    caller = None

    def address_string(self):
        return self.caller or "refused"

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method):
        self.caller = identify(self.request)
        url = urllib.parse.urlsplit(self.path)
        verb = VERBS.get((method, url.path))
        length = int(self.headers.get("Content-Length") or 0)
        if self.caller is None:
            code, answer = 403, {"error": "not a bundle container"}
        elif verb is None:
            code, answer = 404, {"error": f"no verb {method} {url.path}"}
        elif length > MAX_BODY:
            code, answer = 413, {"error": f"body over {MAX_BODY} bytes"}
        else:
            try:
                body = json.loads(self.rfile.read(length)) if length else {}
                code, answer = verb(self.caller, urllib.parse.parse_qs(url.query), body)
            except ValueError as error:
                code, answer = 400, {"error": str(error)}
            except Refused as error:
                code, answer = 409, {"error": str(error)}
        print(
            f"{self.address_string()} {method} {url.path} {code} {answer.get('error', '')}".strip(),
            flush=True,
        )
        data = json.dumps(answer).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    os.umask(0o077)
    server = Server(str(SOCKET), Handler)
    with state_lock:
        state = read_state()
        state["building"] = []
        write_state(state)
    print(f"listening on {SOCKET}", flush=True)
    keep_launching()
    server.serve_forever()


if __name__ == "__main__":
    main()
