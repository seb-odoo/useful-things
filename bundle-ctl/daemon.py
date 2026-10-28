#!/usr/bin/env python3
"""Answer the bundle requests of the dev containers, on a unix socket every bundle container mounts.

    systemctl --user start bundle-ctl
    curl --unix-socket ~/.local/state/bundle-ctl/sock/ctl.sock http://bundle-ctl/whoami

A container reaches neither the host network nor podman, so this is its one way to the host, with
fixed verbs. The caller is the bundle whose container runs the connecting process, read from the
kernel and never from the request.
"""

from concurrent.futures import ThreadPoolExecutor
import contextlib
import datetime
import difflib
import hashlib
import http.server
import importlib.util
import json
import math
import os
import pathlib
import re
import secrets
import shlex
import shutil
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agents import (  # noqa: E402
    EFFORTS,
    LAUNCH_GRACE,
    MODELS,
    OPS,
    PROJECTS,
    SESSION_ID,
    STATE,
    STATE_FILE,
    format_time,
    get_agent,
    get_agent_folder,
    get_bundle_container,
    get_last_text,
    get_mtime,
    get_resume,
    get_session_status,
    get_transcript,
    get_window_pids,
    has_window,
    is_agent_running,
    read_state,
    read_text,
    write_text,
)
from branch_status import (  # noqa: E402
    GIT,
    PR_URL,
    get_github_repo,
    get_local_branches,
    get_open_bundle_folders,
    get_pr,
    get_status,
    git,
    has_write_tree,
)
from commands import (  # noqa: E402
    BUNDLE_NAME,
    MADE,
    drop_stale_containers,
    get_base_for_repo,
    get_base_from_bundle_name,
    get_bundle_name_from_base_and_name,
    get_remote_dev_ref,
    get_remote_ref,
    get_repo_folder,
    get_repos,
    get_sticky_bundles,
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
    get_worktree_container_folder,
)
from config import (  # noqa: E402
    BUNDLE_SUFFIX,
    CLAUDE_CONFIG_CONTAINER,
    HOST_BOX,
    STICKY_BUNDLES,
    WORKTREE_CONTAINER,
    node_modules_key,
)
from utils import UtilsRunner  # noqa: E402

import cpulog  # noqa: E402
import cut  # noqa: E402
import remote_agent  # noqa: E402
import runs  # noqa: E402
import tab_groups  # noqa: E402
import workers  # noqa: E402


def load_config():
    """devcontainer/config.py, under another name than the scripts' own config module."""
    path = HERE.parent / "devcontainer" / "config.py"
    spec = importlib.util.spec_from_file_location("odoo_dev_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load()


def load_held():
    """gateway-core/held.py, the requests hok lists for Seb's yes; None without gateway-core."""
    path = pathlib.Path(CONFIG["REPO_ROOT"]) / "gateway-core" / "held.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("held", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_title():
    """client/window-title.py, whose file name is no module name."""
    spec = importlib.util.spec_from_file_location("title", HERE / "client" / "window-title.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONFIG = load_config()
AFTER = re.compile(r"([01]?[0-9]|2[0-3]):[0-5][0-9]")
AGENT_CPU_STALL = 20
AGENT_GIGABYTES = 2
AGENT_RAMP = 10 * 60
AGENT_RAMP_GIGABYTES = 1
AGENT_TERMINAL = CONFIG.get("AGENT_TERMINAL") or "gnome-terminal --window --title {bundle} --"
BARE_FOLDER = frozenset({".agent", ".claude", ".devcontainer", ".vscode", "odools.toml", "tasks"})
BRANCHES_TIMEOUT = 120
CONTAINER_ID = re.compile(r"/libpod-(?:payload-)?([0-9a-f]{64})")
CPU_LOG = cpulog.CpuLog(get_worktree_container_folder())
DEFAULT_EFFORT = "medium"
DEFAULT_MODEL = "opus"
DELETE_WAIT = 100
DISK_STOP = float(
    os.environ.get("BUNDLE_CTL_DISK_STOP_GB") or CONFIG.get("BUNDLE_CTL_DISK_STOP_GB") or 30,
)
DISK_WARN = float(
    os.environ.get("BUNDLE_CTL_DISK_WARN_GB") or CONFIG.get("BUNDLE_CTL_DISK_WARN_GB") or 60,
)
DISK_WARNED = STATE / "disk-warned"
FREE_AFTER = 5 * 60
GITHUB_ASK = 600
GITHUB_MUX = pathlib.Path.home() / ".local" / "state" / "ssh-github-mux"
GITHUB_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
GO_ON = "Continue from where you left off."
HELD = load_held()
HELD_DIR = STATE / "held"
HELD_LETTER = "b"
HOK_KEPT = 5
HOK_WAITING = pathlib.Path(CONFIG["REPO_ROOT"]) / "host-ctl" / "approve.py"
HOST = "host"
HOST_FOLDER = CONFIG.get("BUNDLE_CTL_HOST_FOLDER")
HOST_ONLY = {("POST", "/close"), ("POST", "/force-queue")}
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
NOT_BUILT = (
    "Nothing of this bundle is checked out yet: /workspace holds no worktree. The base repos are"
    f" read with `git --git-dir={os.path.dirname(get_repo_folder('odoo'))}/<repo>/.git` (show, log"
    " or grep at any ref), and a PR through gh. First see whether the task still needs doing, and"
    " whether reading is enough to do it: then do it and end here with your verdict. When it needs"
    " the code checked out or tests run, `bctl build` (Bash timeout 600000) builds the bundle in"
    " /workspace, and you go on."
)
OPS_OPEN = "ops-window-open"
OPS_TASK = "The agent of the bundle {bundle} sends this task, and Seb said yes to it in hok:\n\n"
PAUSE_WAIT = 120
RECONNECTED = (
    f"{GO_ON}\n\nThe GitHub connection of the host is back: this session waited for it in the"
    " queue since {time}. Go on with what its outage stopped, and first check what changed"
    " meanwhile where your work depends on it (a PR: its threads, its head, its CI; its base)."
)
RESTART_RETRY = 3600
RESUMED = (
    f"{GO_ON}\n\nSeb closed your window and queued this session again at {{time}}: what you left"
    " running in the background (a PR watch, a server, a test) stopped with it. Before anything"
    " else, read again the skill files your task named, as they may have changed, and check what"
    " changed since your last turn where your work depends on it (a PR: its threads, its head, its"
    " CI; its base), as they say for a resume."
)
REVIEW_CHECK = 60
REVIEW_CLOSED = 60 * float(
    os.environ.get("BUNDLE_CTL_REVIEW_CLOSED_MINUTES")
    or CONFIG.get("BUNDLE_CTL_REVIEW_CLOSED_MINUTES")
    or 10,
)
REVIEW_IDLE = 86400 * float(
    os.environ.get("BUNDLE_CTL_REVIEW_IDLE_DAYS") or CONFIG.get("BUNDLE_CTL_REVIEW_IDLE_DAYS") or 2,
)
RUN_CPUS = os.environ.get("BUNDLE_CTL_RUN_CPUS") or CONFIG.get("BUNDLE_CTL_RUN_CPUS")
RUN_MIN_FREE = float(
    os.environ.get("BUNDLE_CTL_RUN_MIN_FREE_GB") or CONFIG.get("BUNDLE_CTL_RUN_MIN_FREE_GB") or 4,
)
RUNS = runs.RunQueue(
    runs.get_pool(runs.read_cores(), RUN_CPUS),
    RUN_MIN_FREE,
    STATE / "runs.json",
)
WORKERS = workers.Workers(
    filter(None, (os.environ.get("BUNDLE_CTL_WORKERS") or CONFIG.get("BUNDLE_CTL_WORKERS") or "")
           .split(",")),
)
# `auto` keeps agents here until set: a first agent must run whole on a worker before.
REMOTE_AUTO = (
    os.environ.get("BUNDLE_CTL_REMOTE_AGENTS") or CONFIG.get("BUNDLE_CTL_REMOTE_AGENTS")
) == "auto"
SESSION_VARIABLES = (
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "SSH_AUTH_SOCK",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
)
SOCKET = STATE / "sock" / "ctl.sock"
START_CHECK = 10
STATUS_FRESH = 30
TERMINAL_CODE = [
    HERE / "agent-terminal.py",
    HERE / "agent-terminator.py",
    HERE / "shift_enter_newline.py",
    *(SCRIPTS / f"{name}.py" for name in ("agents", "commands", "config", "tab_groups")),
]
TERMINAL_FILES = (
    "container",
    "mode",
    "pr-events",
    "prwatch.json",
    "prwatch.lock",
    "prwatch.log",
    "terminal",
)
TITLE = load_title()
WINDOW_CLOSE_WAIT = 30

bundle_locks = {}
bundle_rows = {"read": float("-inf"), "rows": []}
folder_by_container = {}
FOLDER_LABELS = (
    '{{index .Config.Labels "devcontainer.local_folder"}}\t'
    '{{index .Config.Labels "remote-runs.folder"}}'
)
git_statuses = {}
jobs = {}
kept_reviews = {}
package_heads = {}
restarted = {}
ops_lock = threading.Lock()
state_lock = threading.Lock()
status_lock = threading.Lock()
work_lock = threading.Lock()


class Refused(Exception):
    """A request that is valid but cannot run now, answered with a 409."""


def get_container_folder(container_id):
    """The bundle folder of a container, and whether it is the stand-in of a remote agent
    (remote-runs README, "Agents"), which carries the folder in a label of its own.
    """
    if container_id not in folder_by_container:
        res = subprocess.run(
            [
                "podman",
                "inspect",
                "--format",
                FOLDER_LABELS,
                container_id,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        if res.returncode:
            return "", False
        folder, _, remote = res.stdout.strip("\n").partition("\t")
        folder_by_container[container_id] = (folder or remote, bool(remote and not folder))
    return folder_by_container[container_id]


def is_standin(connection):
    """Whether the peer process runs in the stand-in of a remote agent."""
    match = CONTAINER_ID.search(read_cgroup(connection) or "")
    return bool(match) and get_container_folder(match[1])[1]


def read_cgroup(connection):
    """The cgroup of the peer process, None when that process is gone."""
    pid, _uid, _gid = struct.unpack(
        "3i",
        connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")),
    )
    try:
        return pathlib.Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return None


def is_in_container(connection):
    """Whether the peer process runs in a container, the one of HOST_FOLDER included."""
    return "libpod" in (read_cgroup(connection) or "libpod")


def identify(connection):
    """The bundle of the container running the peer process, HOST, or None when refused."""
    cgroup = read_cgroup(connection)
    if cgroup is None:
        return None
    if "libpod" not in cgroup:
        return HOST
    match = CONTAINER_ID.search(cgroup)
    folder = match and get_container_folder(match[1])[0]
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
    """The bundles as read in the last STATUS_FRESH seconds: agents and the helper of
    firefox-gateway poll this, and each read lists the branches of every repo.
    """
    with status_lock:
        if time.monotonic() - bundle_rows["read"] > STATUS_FRESH:
            bundle_rows.update(rows=list_bundles(), read=time.monotonic())
        rows = bundle_rows["rows"]
    with state_lock:
        state = read_state()
    return 200, {
        "agents": count_agents(get_open_windows()),
        "bundles": rows,
        "max_agents": MAX_AGENTS,
        "queue": describe_queue(state),
        "runs": RUNS.describe(),
    }


def read_base_heads(repo, refs):
    """{ref: its commit} of these refs of repo, from one git call: a missing ref is left out."""
    out = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", *refs) or ""
    return dict(line.split(" ") for line in out.splitlines())


def read_statuses(executor, heads, write_tree):
    """The status of each (repo, bundle) of heads, which maps them to their branch commit. On the
    host each git call starts a host-box, so git reads a status again only when the branch or its
    base moved."""
    base_refs = {
        (repo, bundle): get_remote_ref(
            get_base_for_repo(get_base_from_bundle_name(bundle), repo),
            repo,
        )
        for repo, bundle in heads
    }
    repos = sorted({repo for repo, _bundle in heads})
    refs = [{ref for (other, _bundle), ref in base_refs.items() if other == repo} for repo in repos]
    base_heads = dict(zip(repos, executor.map(read_base_heads, repos, refs)))
    keys = {pair: (head, base_heads[pair[0]].get(base_refs[pair])) for pair, head in heads.items()}
    moved = [pair for pair in heads if git_statuses.get(pair, (None,))[0] != keys[pair]]
    for pair, said in zip(moved, executor.map(lambda pair: get_status(*pair, write_tree), moved)):
        git_statuses[pair] = (keys[pair], said)
    for pair in set(git_statuses) - set(heads):
        del git_statuses[pair]
    return {pair: git_statuses[pair][1] for pair in heads}


def list_bundles():
    repos = [repo for repo in get_repos() if os.path.isdir(get_repo_folder(repo))]
    write_tree = has_write_tree()
    repos_by_bundle = {}
    dates = {}
    heads = {}
    with ThreadPoolExecutor() as executor:
        for repo, branches in zip(repos, executor.map(get_local_branches, repos)):
            for branch, date, head, _in_worktree in branches:
                if branch not in get_sticky_bundles(repo):
                    repos_by_bundle.setdefault(branch, []).append(repo)
                    heads[repo, branch] = head
                    reflog = f"{get_repo_folder(repo)}/.git/logs/{get_remote_dev_ref(branch, repo)}"
                    pushed = get_mtime(pathlib.Path(reflog)) or 0
                    dates[branch] = max(dates.get(branch, 0), date, pushed)
        open_future = executor.submit(get_open_bundle_folders)
        statuses = read_statuses(executor, heads, write_tree)
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
        if agent and (remote := remote_agent.read_remote(pathlib.Path(folder))):
            agent["where"] = remote["worker"]
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
    for bundle in sorted(set(state.get("builds", {})) - set(repos_by_bundle)):
        folder = get_worktree_bundle_folder(bundle)
        if not os.path.isdir(folder):
            continue
        is_open = folder in open_folders
        rows.append(
            {
                "agent": get_agent(bundle, 0, is_open),
                "base": get_base_from_bundle_name(bundle),
                "bundle": bundle,
                "folder": True,
                "open": is_open,
                "opening": False,
                "repos": {},
            },
        )
    return rows


def describe_queue(state):
    return [
        dict(
            after=item.get("after") and format_time(item["after"]),
            bundle=item["bundle"],
            effort=item.get("effort"),
            forced=item.get("forced", False),
            model=item.get("model"),
            parent=item["parent"],
            paused=item.get("paused", False),
            priority=item.get("priority", 0),
            built=not item.get("build"),
            queued=item.get("queued") and format_time(item["queued"]),
            resume=item.get("resume", False),
            session=item.get("session"),
            task=item["task"],
            waits_for=item.get("waits_for"),
        )
        for item in state["queue"]
    ]


def describe_memory(open_windows):
    """What the host and the containers have left, and whether it is short for one more agent:
    the agents that began in the last AGENT_RAMP seconds count at AGENT_RAMP_GIGABYTES each, what
    their session and container take, as their tests pass the memory check of the run queue, and
    tasks that stalled on memory in the last minute hold every start back.
    `busy` when tasks waited on the CPU AGENT_CPU_STALL percent of the last minute.
    """
    available, _stall, room = runs.read_memory()
    stall = runs.read_stall(60)
    cpu = runs.read_stall(60, pathlib.Path("/proc/pressure/cpu"))
    ramping = count_ramping(open_windows)
    left = min(available - RUN_MIN_FREE, room) - ramping * AGENT_RAMP_GIGABYTES
    return {
        "available": round(available, 1),
        "busy": cpu >= AGENT_CPU_STALL,
        "cpu": cpu,
        "ramping": ramping,
        "room": None if room == math.inf else round(room, 1),
        "short": left < AGENT_GIGABYTES or stall >= runs.MAX_STALL,
        "stall": stall,
        "swap": round(runs.read_swap(), 1),
    }


def log_memory():
    memory = describe_memory(get_open_windows())
    print(
        "memory "
        + " ".join(
            f"{key}={value}" for key, value in memory.items() if key not in ("busy", "short")
        )
        + " short" * memory["short"]
        + " busy" * memory["busy"],
        flush=True,
    )


def log_cpu():
    folders = get_open_windows()
    kinds = {}
    with RUNS.changed:
        for run in RUNS.running:
            kinds.setdefault(run["bundle"], set()).add(run["kind"])
    line = CPU_LOG.line(
        [os.path.basename(folder) for folder in folders if count_agents([folder])],
        {bundle: "+".join(sorted(kind)) for bundle, kind in kinds.items()},
    )
    if line:
        print(line, flush=True)


def read_disk_free():
    """The GB left on the disk of the bundles."""
    return shutil.disk_usage(get_worktree_container_folder()).free / 2**30


def check_disk():
    free = read_disk_free()
    if free < DISK_STOP:
        raise Refused(
            f"{free:.0f} GB left on the disk, under {DISK_STOP:.0f}: no new bundle until Seb frees"
            " space, his desktop says so",
        )


def notify(summary, body):
    subprocess.run(
        ["notify-send", "--urgency=critical", "--app-name=bundle-ctl", "--", summary, body],
        capture_output=True,
        check=False,
        env=session_env(),
        stdin=subprocess.DEVNULL,
        timeout=10,
    )


def warn_disk():
    """Tell Seb on his desktop when the disk goes under DISK_WARN, then at each 10 GB less, until
    it is 10 GB over DISK_WARN again. The level warned is kept in DISK_WARNED, so a restart of the
    daemon does not warn again.
    """
    try:
        free = read_disk_free()
        if free >= DISK_WARN + 10:
            DISK_WARNED.unlink(missing_ok=True)
            return
        level = int(free // 10)
        warned = read_text(DISK_WARNED)
        if free >= DISK_WARN or (warned and level >= int(warned)):
            return
        DISK_WARNED.write_text(f"{level}\n")
        print(f"disk {free:.0f} GB left", flush=True)
        held = (
            f"New bundles and agent starts wait until {DISK_STOP:.0f} GB are free."
            if free < DISK_STOP
            else f"Under {DISK_STOP:.0f} GB, new bundles and agent starts will wait."
        )
        notify(
            f"{free:.0f} GB left on the disk",
            f"{held} gbs lists the bundles whose PR is merged or closed: each one deleted frees"
            " about 3 GB.",
        )
    except (OSError, subprocess.SubprocessError):
        traceback.print_exc()


def get_queue(caller, query, body):
    with state_lock:
        state = read_state()
    open_windows = get_open_windows()
    return 200, {
        "agents": count_agents(open_windows),
        "max_agents": MAX_AGENTS,
        "memory": describe_memory(open_windows),
        "queue": describe_queue(state),
    }


def force_queue(caller, query, body):
    """Let the tasks queued now start over the cap, those that wait for their time, for a manual
    resume or for GitHub aside, or the one of a bundle, its time, its pause and its wait for GitHub
    too. The ones queued later wait for a slot."""
    bundle = body.get("bundle")
    with state_lock:
        state = read_state()
        now = time.time()
        items = [
            item
            for item in state["queue"]
            if item["bundle"] == bundle
            or (
                bundle is None
                and (item.get("after") or 0) <= now
                and not item.get("paused")
                and not item.get("github")
            )
        ]
        if bundle is not None and not items:
            raise ValueError(f"{bundle} is not queued")
        for item in items:
            item["forced"] = True
            item.pop("after", None)
            item.pop("github", None)
            item.pop("paused", None)
        write_state(state)
    forced = [item["bundle"] for item in items]
    launch_queued()
    code, answer = get_queue(caller, query, body)
    left = {item["bundle"] for item in answer["queue"]}
    return code, dict(answer, forced=forced, started=[name for name in forced if name not in left])


def get_queued_task(queue, caller, bundle, verb):
    """The first task queued for a bundle that the caller may change: the host, that bundle or the
    one that queued it asks."""
    items = [item for item in queue if item["bundle"] == bundle]
    if not items:
        raise ValueError(f"{bundle} is not queued")
    item = next((item for item in items if caller in (HOST, bundle, item["parent"])), None)
    if item is None:
        raise Refused(f"a bundle {verb} its own task or a task it queued only")
    return item


def unqueue(caller, query, body):
    """Drop the queued task of a bundle. The host, the bundle or the one that queued it asks."""
    bundle = body.get("bundle") or ""
    with state_lock:
        state = read_state()
        item = get_queued_task(state["queue"], caller, bundle, "drops")
        state["queue"].remove(item)
        state.setdefault("relaunches", {})[bundle] = MAX_RELAUNCHES
        write_state(state)
    return 200, {"bundle": bundle, "parent": item["parent"], "queued": format_time(item["queued"])}


def move_task(caller, query, body):
    """Move the queued task of a bundle to a place of the queue, from 1. It keeps its priority when
    that priority fits between the tasks around its new place, else takes the nearest of theirs, so
    that the queue stays in the order of priority. The host, that bundle or the one that queued
    the task asks."""
    bundle, place = body.get("bundle") or "", body.get("place")
    with state_lock:
        state = read_state()
        queue = state["queue"]
        if type(place) is not int or not 1 <= place <= len(queue):
            raise ValueError(f"place: an integer from 1 to {len(queue)}")
        item = get_queued_task(queue, caller, bundle, "moves")
        queue.remove(item)
        queue.insert(place - 1, item)
        priority = item.get("priority", 0)
        if place > 1:
            priority = min(priority, queue[place - 2].get("priority", 0))
        if place < len(queue):
            priority = max(priority, queue[place].get("priority", 0))
        item["priority"] = priority
        write_state(state)
    launch_queued()
    return get_queue(caller, query, body)


def schedule_task(caller, query, body):
    """Set when the queued task of a bundle may leave the queue: `spot`, once a slot and the
    memory let it, `manual`, once it is scheduled again, or `HH:MM`, once that time of the host
    came. It no longer goes over the cap nor waits for GitHub. The host, that bundle or the one
    that queued the task asks."""
    bundle, when = body.get("bundle") or "", body.get("when")
    if when not in ("manual", "spot") and not (isinstance(when, str) and AFTER.fullmatch(when)):
        raise ValueError("when: spot, manual or a time of the day, HH:MM")
    with state_lock:
        state = read_state()
        item = get_queued_task(state["queue"], caller, bundle, "schedules")
        item["after"] = None if when in ("manual", "spot") else get_next_time(when)
        item["paused"] = when == "manual"
        item.pop("forced", None)
        item.pop("github", None)
        write_state(state)
    launch_queued()
    return get_queue(caller, query, body)


def requeue_agent(caller, query, body):
    """Queue the session of a bundle's agent again, last, paused with `paused`, and close its
    window once its turn ended, PAUSE_WAIT seconds at most. Meanwhile its Bash calls are refused on
    a reason that tells it to end its turn (client/run-hook.py). The host asks, or with `github`
    the agent itself: its session then waits for the GitHub connection of the host."""
    bundle = body.get("bundle") or ""
    if caller != HOST and not (caller == bundle and body.get("github") is True):
        raise Refused(
            "requeue: only the host and the ops container stop an agent, and an agent itself"
            " with github",
        )
    if set(body) - {"bundle", "github", "paused"} or any(
        type(body.get(key, False)) is not bool for key in ("github", "paused")
    ):
        raise ValueError("requeue: a bundle, and github or paused true or false")
    if not BUNDLE_NAME.fullmatch(bundle) or not os.path.isdir(get_worktree_bundle_folder(bundle)):
        raise ValueError(f"no bundle folder for {bundle}")
    session = read_text(get_agent_folder(bundle) / "session")
    if not session or not get_window_pids(bundle):
        raise ValueError(f"{bundle} has no agent window")
    paused, github = body.get("paused", False), body.get("github", False)
    with state_lock:
        priority = min((item.get("priority", 0) for item in read_state()["queue"]), default=0)
    task = {"github": github, "paused": paused, "priority": priority, "resume": True, "task": GO_ON}
    check_task(bundle, task)
    answer = enqueue(caller, bundle, task)
    why = "when Seb put it back in the queue"
    if paused:
        why = "when Seb paused it"
    elif github:
        why = "when it went back to the queue to wait for the GitHub connection of the host"
    threading.Thread(target=stop_after_turn, args=(bundle, session, why), daemon=True).start()
    return 202, {**answer, "wait": PAUSE_WAIT}


def close_agent_window(caller, query, body):
    """Close the agent window of a bundle as close_freed does, once its claude is idle: refused
    while a claude of the bundle is in a turn or waits for an answer, as for a deletion, and while
    its agent starts, before its claude runs. Only the host asks (HOST_ONLY)."""
    bundle = body.get("bundle") or ""
    if set(body) != {"bundle"} or not BUNDLE_NAME.fullmatch(bundle):
        raise ValueError("close: a bundle name alone")
    folder = get_worktree_bundle_folder(bundle)
    if not os.path.isdir(folder):
        raise ValueError(f"no bundle folder for {bundle}")
    if not get_window_pids(bundle):
        raise ValueError(f"{bundle} has no agent window")
    if remote_agent.is_remote(pathlib.Path(folder)):
        raise Refused(f"{bundle}: its agent runs on a worker, this host cannot see its turn")
    if is_in_turn(bundle):
        raise Refused(f"{bundle}: its claude is in a turn or waits for an answer")
    live = cut.get_live_sessions([pathlib.Path(CLAUDE_CONFIG_CONTAINER, bundle, "sessions")])
    if is_agent_running(bundle) and read_text(get_agent_folder(bundle) / "session") not in live:
        raise Refused(f"{bundle}: its agent is starting, its claude does not run yet")
    get_agent_folder(bundle).mkdir(exist_ok=True)
    close_freed(bundle)
    if get_window_pids(bundle):
        raise Refused(f"{bundle}: its agent window is still open {WINDOW_CLOSE_WAIT} s later")
    return 200, {"bundle": bundle}


def stop_after_turn(bundle, session, why):
    """Close the agent window of a bundle once the turn of its session ended, or at PAUSE_WAIT.
    When that cut the turn, the queued task tells the session so and why, as for a session a crash
    cut."""
    pause = get_agent_folder(bundle) / "pause"
    deadline = time.time() + PAUSE_WAIT
    write_text(pause, f"{deadline}\n")
    try:
        busy = get_session_status(bundle, session) == "busy"
        while busy and get_window_pids(bundle) and time.time() < deadline:
            time.sleep(1)
            busy = get_session_status(bundle, session) == "busy"
        try:
            transcript = get_transcript(session)
            row = transcript and cut.read_session(transcript)
        except OSError:
            row = None
        if row and (busy or cut.get_cut(row)):
            prompt = cut.get_prompt({"call": row["call"], "stopped": time.time()}, why)
            with state_lock:
                state = read_state()
                for item in state["queue"]:
                    if item["bundle"] == bundle and item.get("resume") and item["task"] == GO_ON:
                        item["task"] = prompt
                write_state(state)
        close_window(bundle)
        deadline = time.monotonic() + WINDOW_CLOSE_WAIT
        while get_window_pids(bundle) and time.monotonic() < deadline:
            time.sleep(0.5)
    finally:
        pause.unlink(missing_ok=True)


def list_runs(caller, query, body):
    remote, silent = WORKERS.list_jobs()
    return 200, RUNS.describe() | {"remote": remote, "silent": silent}


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


def run_in_session(log, *command, scope=True, setenv=None):
    """Run a command of the graphical session in a unit of its own, with the variables of setenv.

    Started here, it would die with the daemon's cgroup. A scope waits for the command to end, a
    service does not.
    """
    setenv = setenv or {}
    env = session_env() | setenv
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        raise Refused("no graphical session to open a window in")
    keys = [*SESSION_VARIABLES, *setenv]
    session = [f"--setenv={key}={env[key]}" for key in keys if key in env]
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
    folder = HOST_FOLDER if bundle == "ops" else get_worktree_bundle_folder(bundle)
    drop_stale_containers(folder, log)
    run_in_session(log, "code", "--folder-uri", UtilsRunner()._devcontainer_folder_uri(folder))


def open_terminal(log, bundle, takeover=None):
    """takeover: the line the new terminal shows when it takes the session from an old one."""
    log.write(f"starting the agent of {bundle} in a terminal\n")
    log.flush()
    terminal = shlex.split(AGENT_TERMINAL.format(bundle=bundle))
    agent = [sys.executable, str(HERE / "agent-terminal.py"), bundle]
    setenv = {"BUNDLE_CTL_TAKEOVER": takeover} if takeover else None
    run_in_session(log, *terminal, *agent, scope=False, setenv=setenv)


def run_job(job, work, locks):
    with contextlib.ExitStack() as held:
        for lock in locks:
            held.enter_context(lock)
        log = held.enter_context(open(job["log"], "a", buffering=1))
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


def start_job(caller, verb, work, lock=work_lock, bundle=None):
    """The job runs under lock, None for none, and after the jobs of its bundle that came first."""
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
    locks = [bundle and bundle_locks.setdefault(bundle, threading.Lock()), lock]
    threading.Thread(target=run_job, args=(job, work, filter(None, locks)), daemon=True).start()
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
    """The agents at work in a terminal: one that gave its verdict leaves its slot."""
    return sum(
        os.path.exists(f"{folder}/.agent/terminal") and not os.path.exists(f"{folder}/.agent/done")
        for folder in folders
    )


def count_ramping(folders):
    """The agents that began their run in the last AGENT_RAMP seconds and gave no verdict yet."""
    now = time.time()
    return sum(
        now - (get_mtime(pathlib.Path(folder, ".agent/run.lock")) or 0) < AGENT_RAMP
        and not os.path.exists(f"{folder}/.agent/done")
        for folder in folders
    )


def is_held(agent):
    """Whether a live terminal holds the session of this `.agent` folder."""
    try:
        cmdline = pathlib.Path(f"/proc/{read_text(agent / 'terminal')}/cmdline").read_bytes()
    except OSError:
        return False
    return agent.parent.name.encode() in cmdline.split(b"\0")


def check_terminal(caller, bundle, body):
    """Why the agent terminal of a bundle cannot open on its last session, with no prompt, or for
    the host on a new session with no task when the bundle has no agent.

    The window of the bundle closes for it: only that bundle or the host may ask.
    """
    if "task" in body or body.get("resume"):
        raise ValueError("terminal: only without a task")
    agent = get_agent_folder(bundle)
    started = (agent / "task.md").is_file() and (agent / "session").exists()
    new = caller == HOST and not (agent / "task.md").exists() and not (agent / "session").exists()
    if not started and not new:
        raise Refused(f"{bundle} has no agent session to open in a terminal")
    if is_held(agent):
        raise Refused(f"{bundle}: a terminal holds its session, a new one would cut its turn")
    container = get_bundle_container(bundle)
    if caller not in (HOST, bundle) and container and has_window(container):
        raise Refused(f"{bundle} is open in a window: its session goes on in its Claude tab")


def check_task(bundle, body, session=None):
    if "task" not in body:
        if "after" in body:
            raise ValueError("after: only with a task")
        if "effort" in body:
            raise ValueError("effort: only with a task")
        if "model" in body:
            raise ValueError("model: only with a task")
        if "priority" in body:
            raise ValueError("priority: only with a task")
        if body.get("resume"):
            raise ValueError("resume: only with a task")
        if "paused" in body:
            raise ValueError("paused: only with a task")
        if "where" in body:
            raise ValueError("where: only with a task")
        return
    task, priority = body["task"], body.get("priority", 0)
    if type(body.get("paused", False)) is not bool:
        raise ValueError("paused: true or false")
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK:
        raise ValueError(f"task: a text of at most {MAX_TASK} characters")
    if "after" in body and not (isinstance(body["after"], str) and AFTER.fullmatch(body["after"])):
        raise ValueError("after: a time of the day, HH:MM")
    if type(priority) is not int or abs(priority) > MAX_PRIORITY:
        raise ValueError(f"priority: an integer from -{MAX_PRIORITY} to {MAX_PRIORITY}")
    if body.get("effort", DEFAULT_EFFORT) not in EFFORTS:
        raise ValueError(f"effort: one of {', '.join(EFFORTS)}")
    if body.get("model", DEFAULT_MODEL) not in MODELS:
        raise ValueError(f"model: one of {', '.join(MODELS)}")
    if body.get("where", "auto") not in ("auto", "local", "worker"):
        raise ValueError("where: auto, local or worker")
    with state_lock:
        state = read_state()
    queued = [item for item in state["queue"] if item["bundle"] == bundle]
    if queued and (body.get("resume") or any(item.get("build") for item in queued)):
        raise Refused(f"{bundle} is already queued")
    if body.get("resume"):
        if not session and not (get_agent_folder(bundle) / "session").exists():
            raise Refused(f"{bundle} has no session to resume")
        container = get_bundle_container(bundle)
        if container and has_window(container):
            raise Refused(f"{bundle} is open in a window: its session goes on in its Claude tab")


def get_task_priority(bundle):
    if bundle == HOST:
        return 0
    text = read_text(get_agent_folder(bundle) / "priority") or "0"
    return max(-MAX_PRIORITY, min(int(text), MAX_PRIORITY))


def get_next_time(text):
    """The next HH:MM of the host, today or tomorrow."""
    hour, minute = map(int, text.split(":"))
    now = datetime.datetime.now()
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (start if start > now else start + datetime.timedelta(days=1)).timestamp()


def enqueue(caller, bundle, body, build=None, session=None):
    """Queue the task of a bundle. `build` is the script that makes a bundle not made yet: the
    agent starts in its bare folder and runs it with `bctl build` once it needs the code. With no
    priority, the task of an agent takes the one of the agent's own task. `session` is the session
    a resume goes on with, when it is not the last one of the agent: never taken from a request,
    as the sessions of every bundle share one folder."""
    priority = body["priority"] if "priority" in body else get_task_priority(caller)
    after = get_next_time(body["after"]) if "after" in body else None
    effort, model, queued = body.get("effort"), body.get("model"), time.time()
    with state_lock:
        state = read_state()
        queue = state["queue"]
        position = next(
            (index for index, item in enumerate(queue) if item.get("priority", 0) < priority),
            len(queue),
        )
        behind = [index + 1 for index, item in enumerate(queue) if item["bundle"] == bundle]
        position = max([position, *behind])
        queue.insert(
            position,
            {
                "after": after,
                "build": build,
                "bundle": bundle,
                "effort": None if effort == DEFAULT_EFFORT else effort,
                "model": None if model == DEFAULT_MODEL else model,
                "parent": caller,
                "paused": body.get("paused", False),
                "priority": priority,
                "queued": queued,
                "resume": bool(body.get("resume")),
                "task": body["task"],
                **({"where": body["where"]} if body.get("where") else {}),
                **({"github": True} if body.get("github") else {}),
                **({"session": session} if session else {}),
            },
        )
        state.get("relaunches", {}).pop(bundle, None)
        if bundle in state.get("deletions", []):
            state["deletions"].remove(bundle)
            state.get("drops", {}).pop(bundle, None)
            print(f"{bundle} is kept: a task came after the yes to its deletion", flush=True)
        write_state(state)
    launch_queued()
    with state_lock:
        queue = read_state()["queue"]
    for place, item in enumerate(queue, 1):
        if item["bundle"] == bundle and item["queued"] == queued:
            return {
                "bundle": bundle,
                "priority": priority,
                "queued": place,
                "waits_for": item.get("waits_for"),
            }
    return {"bundle": bundle, "priority": priority, "started": True}


def is_busy(bundle, alive):
    """Whether an agent runs or waits in the bundle: a task with no verdict, in a container that
    runs or was just launched.
    """
    folder = get_agent_folder(bundle)
    return alive and (folder / "task.md").is_file() and not (folder / "done").exists()


def get_alive_launches(launches):
    """The launches still alive after a restart of the daemon. The spawn of a queued task dies with
    the daemon until its terminal starts: the stranded check queues the task again. A window opened
    by hand is recorded once open, after its task.md."""
    alive = {}
    for bundle, launched in launches.items():
        folder = get_agent_folder(bundle)
        if (folder / "terminal").exists() or (get_mtime(folder / "task.md") or 0) < launched - 1:
            alive[bundle] = launched
    return alive


def get_starting(launches, open_windows, now):
    """The bundle of an agent that was launched and has not begun its run, None when no such.

    A terminal that begins no run counts for LAUNCH_GRACE, as a launch with no container does.
    """
    held = sorted(os.path.basename(folder) for folder in open_windows)
    for bundle in (*launches, *held):
        folder = get_agent_folder(bundle)
        launched = launches.get(bundle) or get_mtime(folder / "terminal") or 0
        if (
            now - launched < LAUNCH_GRACE
            and (folder / "task.md").is_file()
            and not (folder / "run.lock").exists()
            and not (folder / "done").exists()
        ):
            return bundle
    return None


def get_resumed(item):
    """The prompt of a task: a session queued again with no prompt of its own learns that its
    window closed meanwhile, with what it ran in the background."""
    if item.get("resume") and item["task"] == GO_ON:
        text = RECONNECTED if item.get("github") else RESUMED
        return text.format(time=format_time(item["queued"]))
    return item["task"]


def ask_github():
    """Have the host open its GitHub connection again, which Bitwarden asks Seb: GITHUB_ASK
    seconds apart at most, while a task waits for it."""
    start = GITHUB_MUX / "request" / "start"
    if time.time() - (get_mtime(start) or 0) >= GITHUB_ASK:
        start.parent.mkdir(parents=True, exist_ok=True)
        start.write_text(f"{time.time()}\n")


def is_deleting(bundle):
    return any(
        job["verb"] == f"delete {bundle}" and job["state"] in ("waiting", "running")
        for job in jobs.values()
    )


def write_task(item, keep=()):
    folder = get_agent_folder(item["bundle"])
    if folder.is_symlink():
        folder.unlink()
    if folder.exists() and (
        (folder / "session").exists() or read_text(folder / "task.md") != item["task"].strip()
    ):
        kept = {"history", "where", *keep, *(("session",) if item.get("resume") else ())}
        move_to_history(folder, kept)
    folder.mkdir(exist_ok=True)
    write_text(folder / "parent", f"{item['parent']}\n")
    if "queued" in item:
        write_text(folder / "queued", f"{item['queued']}\n")
    write_text(folder / "priority", f"{item.get('priority', 0)}\n")
    write_text(folder / "task.md", item["task"])
    if item.get("session"):
        write_text(folder / "session", f"{item['session']}\n")
    for name in ("effort", "model"):
        if item.get(name):
            write_text(folder / name, f"{item[name]}\n")
        else:
            (folder / name).unlink(missing_ok=True)
    if item.get("where") in ("local", "worker"):
        write_text(folder / "where", f"{item['where']}\n")


def move_to_history(folder, kept):
    """Move the files of an `.agent/` folder but `kept` to a new folder of its history, through
    descriptors of folders that are no link: the bundle writes all of them, and a `history` that
    is no folder is replaced."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    stamp = time.strftime("%Y%m%d-%H%M%S")
    agent = os.open(folder, flags)
    try:
        with contextlib.suppress(FileExistsError):
            os.mkdir("history", dir_fd=agent)
        if not stat.S_ISDIR(os.stat("history", dir_fd=agent, follow_symlinks=False).st_mode):
            os.unlink("history", dir_fd=agent)
            os.mkdir("history", dir_fd=agent)
        history = os.open("history", flags, dir_fd=agent)
        try:
            os.mkdir(stamp, dir_fd=history)
            moved = os.open(stamp, flags, dir_fd=history)
        finally:
            os.close(history)
        try:
            for name in set(os.listdir(agent)) - set(kept):
                os.rename(name, name, src_dir_fd=agent, dst_dir_fd=moved)
        finally:
            os.close(moved)
    finally:
        os.close(agent)


def launch_queued():
    """Start what the queue can start now. Return whether a task waits for an agent to start."""
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
                        "effort": read_text(task.parent / "effort"),
                        "model": read_text(task.parent / "model"),
                        "parent": parent,
                        "priority": get_task_priority(bundle),
                        "queued": float(read_text(task.parent / "queued") or now),
                        "task": task.read_text(),
                    },
                )
        state["queue"][:0] = stranded
        env = session_env()
        has_display = env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")
        starting = get_starting(state["launches"], open_windows, now)
        free = read_disk_free()
        memory = describe_memory(open_windows)
        paced = False
        queue = []
        for item in state["queue"]:
            bundle = item["bundle"]
            folder = get_agent_folder(bundle)
            if not folder.parent.is_dir() and not item.get("build"):
                print(f"dropped the task of {bundle}: its folder is gone", flush=True)
                continue
            # A pass that dies after a launch saves no state, and the next one reads it queued.
            if (folder / "session").exists() and (
                read_text(folder / "queued") == str(item["queued"])
            ):
                continue
            is_running = get_worktree_bundle_folder(bundle) in open_windows
            container = is_running and get_bundle_container(bundle)
            fits = count_agents(open_windows) + len(state["launches"]) < MAX_AGENTS
            has_room = item.get("forced") or (fits and not memory["short"] and not memory["busy"])
            can_start = has_display and (is_running or has_room)
            in_terminal = (folder / "terminal").exists() and (container or is_held(folder))
            earlier = next((kept for kept in queue if kept["bundle"] == bundle), None)
            if earlier:
                item["waits_for"] = (
                    f"the task queued before it for the bundle to start (from {earlier['parent']},"
                    f" since {format_time(earlier['queued'])})"
                )
            elif item.get("paused"):
                item["waits_for"] = "a manual resume"
            elif is_deleting(bundle):
                item["waits_for"] = "the deletion of its checkout"
            elif is_busy(bundle, is_running or bundle in state["launches"]):
                item["waits_for"] = get_previous_end(folder)
            elif free < DISK_STOP and (
                in_terminal or not get_agent_worker(item, folder, is_running)[0]
            ):
                item["waits_for"] = f"disk space ({free:.0f} GB left, {DISK_STOP:.0f} needed)"
            elif in_terminal and not (waits_for := get_terminal_wait(item, now)):
                hand_to_terminal(item)
                continue
            elif in_terminal:
                item["waits_for"] = waits_for
            elif container and has_window(container):
                # The claude-autoopen extension of the window starts it.
                item["task"] = get_resumed(item)
                write_task(item)
                continue
            elif (item.get("after") or 0) > now:
                item["waits_for"] = f"its time, {format_time(item['after'])}"
            elif item.get("github") and not (GITHUB_MUX / "sock" / "github").is_socket():
                item["waits_for"] = "the GitHub connection of the host"
                ask_github()
            elif remote_agent.is_remote(folder.parent):
                item["waits_for"] = "the end of its agent on its worker"
            elif (worker := get_agent_worker(item, folder, is_running))[0]:
                item["task"] = get_resumed(item)
                write_task(item)
                start_job(
                    item["parent"],
                    "remote-agent",
                    lambda log, bundle=bundle, worker=worker[0]: start_remote(log, bundle, worker),
                    lock=None,
                    bundle=bundle,
                )
                continue
            elif worker[1]:
                item["waits_for"] = worker[1]
            elif can_start and starting:
                item["waits_for"] = f"the agent of {starting} to start"
                paced = True
            elif can_start:
                item["task"] = get_resumed(item)
                if item.get("build"):
                    prepare_folder(bundle)
                    state.setdefault("builds", {})[bundle] = item.pop("build")
                    item["task"] = f"{NOT_BUILT}\n\n{item['task']}"
                write_task(item)
                if not is_running:
                    state["launches"][bundle] = now
                start_job(
                    item["parent"],
                    "spawn",
                    lambda log, bundle=bundle: open_terminal(log, bundle),
                    lock=None,
                    bundle=bundle,
                )
                starting = bundle
                continue
            elif not has_display:
                item["waits_for"] = "a desktop session"
            elif not fits:
                item["waits_for"] = "a slot"
            elif memory["short"]:
                item["waits_for"] = "memory on the host"
            else:
                item["waits_for"] = "the CPU of the host"
            queue.append(item)
        state["queue"] = queue
        write_state(state)
    return paced


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
        delete_safely()
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
                if status == "died":
                    delete_safely()
            proc.wait()
            time.sleep(5)

    def on_timer():
        checked = float("-inf")
        while True:
            warn_disk()
            delete_safely()
            restart_safely()
            free_safely()
            try:
                keep_remote_agents()
            except Exception:
                traceback.print_exc()
            if time.monotonic() - checked > REVIEW_CHECK:
                checked = time.monotonic()
                drop_reviews_safely()
                log_memory()
                log_cpu_safely()
                build_node_modules_safely()
            time.sleep(START_CHECK if launch_safely() else 60)

    threading.Thread(target=on_events, daemon=True).start()
    threading.Thread(target=on_timer, daemon=True).start()


def launch_safely():
    try:
        paced = launch_queued()
    except Exception:
        traceback.print_exc()
        return False
    return paced


def get_agent_worker(item, folder, is_running):
    """(the worker that starts the agent of this task, why it waits): a task goes `auto` by
    default, to a worker with an agent place, else here; one placed on a worker waits for it
    (README.md, "Remote agents"). A bundle open in a window stays here, and so does one not built
    yet, as its build runs here.
    """
    if is_running or item.get("build") or not WORKERS.names:
        return None, None
    where = item.get("where") or read_text(folder / "where") or "auto"
    if where == "local" or (where == "auto" and not REMOTE_AUTO):
        return None, None
    wanted = where.removeprefix("worker:") if where.startswith("worker:") else None
    found = WORKERS.choose_agent(wanted)
    if found or where == "auto":
        return found, None
    return None, f"a place for an agent on {wanted or 'a worker'}"


def start_remote(log, bundle, worker):
    """The agent of the task on WORKER, else here as any task, as it started nowhere."""
    agent = get_agent_folder(bundle)
    session = read_text(agent / "session") or str(uuid.uuid4())
    write_text(agent / "session", f"{session}\n")
    transcript = pathlib.Path("~/.claude/projects/-workspace").expanduser() / f"{session}.jsonl"
    folder = pathlib.Path(get_worktree_bundle_folder(bundle))
    try:
        remote_agent.start(
            log, bundle, folder, worker, WORKERS, get_base_from_bundle_name(bundle), session,
            transcript.exists(),
        )
    except Exception as error:
        log.write(traceback.format_exc())
        print(f"{bundle}: its agent starts here, not on {worker}: {error}", flush=True)
        task = {"task": read_text(agent / "task.md") or GO_ON, "where": "local"}
        if transcript.exists():
            task["resume"] = True
        else:
            (agent / "session").unlink(missing_ok=True)
        enqueue(read_text(agent / "parent") or HOST, bundle, task)
        return
    print(f"{bundle}: its agent started on {worker}", flush=True)


def keep_remote_agents():
    """Each remote agent: its tunnel up, its end once its job ended, and its job ended once it
    gave its verdict FREE_AFTER ago, as a terminal closes here.
    """
    root = pathlib.Path(get_worktree_container_folder())
    remotes = sorted(root.glob("*/*/.agent/remote.json"))
    if remotes:
        remote_agent.renew_login_soon(remotes[0].parent.parent)
    for found in remotes:
        folder = found.parent.parent
        bundle = folder.name
        try:
            line, lost = remote_agent.keep(bundle, folder, WORKERS)
            done = get_mtime(found.parent / "done")
            session = read_text(found.parent / "session")
            if line is None and done and time.time() - done > FREE_AFTER and (
                get_session_status(bundle, session) != "busy"
            ):
                line = remote_agent.end(bundle, folder)
        except Exception:
            traceback.print_exc()
            continue
        if line:
            print(line, flush=True)
        if lost and not (found.parent / "done").exists():
            transcript = PROJECTS / "-workspace" / f"{read_text(found.parent / 'session')}.jsonl"
            if transcript.exists():
                enqueue(HOST, bundle, {"task": GO_ON, "resume": True})
            else:
                enqueue(HOST, bundle, {"task": read_text(found.parent / "task.md") or GO_ON})


def bring_back(caller, query, body):
    """The agent of a bundle back from its worker, its session going on in a terminal here
    (README.md, "Remote agents"). The host or that bundle asks."""
    bundle = body.get("bundle") or ""
    if caller not in (HOST, bundle):
        raise Refused("bring-back: only the host and the bundle itself")
    if not BUNDLE_NAME.fullmatch(bundle) or not os.path.isdir(get_worktree_bundle_folder(bundle)):
        raise ValueError(f"no bundle folder for {bundle}")
    folder = pathlib.Path(get_worktree_bundle_folder(bundle))
    if not remote_agent.is_remote(folder):
        raise Refused(f"the agent of {bundle} runs here already")

    def work(log):
        log.write(remote_agent.move_back(log, bundle, folder, WORKERS) + "\n")
        open_terminal(log, bundle, takeover=f"{bundle}: back from its worker, same session")

    return start_job(caller, "bring-back", work, lock=None, bundle=bundle)


def delete_safely():
    try:
        close_deleted()
        delete_stopped()
    except Exception:
        traceback.print_exc()


def restart_safely():
    try:
        restart_old_terminal()
    except Exception:
        traceback.print_exc()


def free_safely():
    try:
        free_done_terminals()
        requeue_failed_terminals()
    except Exception:
        traceback.print_exc()


def drop_reviews_safely():
    try:
        drop_idle_reviews()
    except Exception:
        traceback.print_exc()


def log_cpu_safely():
    try:
        log_cpu()
    except Exception:
        traceback.print_exc()


def local_branches(bundle):
    return [
        repo
        for repo in get_repos()
        if subprocess.run(
            [*GIT, "rev-parse", "--verify", "--quiet", f"refs/heads/{bundle}"],
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
            [*GIT, "log", "--oneline", f"refs/heads/{bundle}", "--not", "--remotes"],
            capture_output=True,
            check=False,
            cwd=get_repo_folder(repo),
            text=True,
        ).stdout.strip()
        if unpushed:
            raise Refused(f"{bundle} has unpushed commits in {repo}, {script} would reset them")


def get_branch_prs(repo, branch):
    """The PRs whose head is this branch, of any state: their state by `owner/name#number`."""
    github_repo = get_github_repo(repo)
    res = subprocess.run(
        ["gh", "pr", "list", "-R", github_repo, "--head", branch, "--state", "all"]
        + ["--json", "number,state"],
        capture_output=True,
        check=False,
        text=True,
    )
    if res.returncode:
        raise Refused(f"gh failed on the PRs of {branch}: {res.stderr.strip()}")
    return {f"{github_repo}#{pr['number']}": pr["state"].lower() for pr in json.loads(res.stdout)}


def get_cherry(bundle, repo):
    """The commits of the branch over its base, each with whether the base has its change under
    another sha (same patch-id). Empty when the base is not fetched.
    """
    base = get_remote_ref(get_base_for_repo(get_base_from_bundle_name(bundle), repo), repo)
    lines = subprocess.run(
        [*GIT, "cherry", base, f"refs/heads/{bundle}"],
        capture_output=True,
        check=False,
        cwd=get_repo_folder(repo),
        text=True,
    ).stdout.splitlines()
    return {line[2:]: line.startswith("-") for line in lines}


def get_others_commits(bundle, repo, drop=()):
    """The committers of the commits of the branch no remote has, refused when one is Seb and its
    base does not have its change, unless the commit is in `drop`.
    """
    folder = get_repo_folder(repo)
    commits = [
        line.split()
        for line in subprocess.run(
            [*GIT, "log", "--format=%H %ce", f"refs/heads/{bundle}", "--not", "--remotes"],
            capture_output=True,
            check=False,
            cwd=folder,
            text=True,
        ).stdout.splitlines()
    ]
    me = subprocess.run(
        [*GIT, "config", "user.email"],
        capture_output=True,
        check=False,
        cwd=folder,
        text=True,
    ).stdout.strip()
    cherry = get_cherry(bundle, repo) if commits else {}
    work = [committer for sha, committer in commits if not cherry.get(sha) and sha not in drop]
    if work and (not me or me in work):
        raise Refused(f"{bundle} has commits in {repo} that no remote has")
    return [committer for _sha, committer in commits]


def check_uncommitted(bundle):
    """Refused when a worktree of the bundle has a file not committed, which --force would drop."""
    for repo in get_repos():
        worktree = get_worktree_bundle_repo_folder(bundle, repo)
        if os.path.isdir(worktree) and subprocess.run(
            [*GIT, "status", "--porcelain"],
            capture_output=True,
            check=False,
            cwd=worktree,
            text=True,
        ).stdout.strip():
            raise Refused(f"{bundle} has uncommitted files in {repo}")


def get_dropped(bundle, commits):
    """The commits a deletion may lose, as full sha and subject by repo: each one named must be a
    commit of the branch that no remote has.
    """
    if bad := [commit for commit in commits if not re.fullmatch(r"[0-9a-f]{7,40}", commit)]:
        raise ValueError(f"not a commit sha of 7 characters or more: {', '.join(bad)}")
    dropped, left = {}, set(commits)
    for repo in local_branches(bundle):
        for line in subprocess.run(
            [*GIT, "log", "--format=%H %s", f"refs/heads/{bundle}", "--not", "--remotes"],
            capture_output=True,
            check=False,
            cwd=get_repo_folder(repo),
            text=True,
        ).stdout.splitlines():
            sha, _, subject = line.partition(" ")
            if named := {commit for commit in commits if sha.startswith(commit)}:
                dropped.setdefault(repo, []).append((sha, subject))
                left -= named
    if left:
        raise ValueError(f"no commit of {bundle} that no remote has: {', '.join(sorted(left))}")
    return dropped


def check_deletion(bundle, drop=()):
    """What the deletion drops, a line by repo where the bundle has its branch, and whether
    delete_bundle needs --force. Refused while a repo holds work: a commit of Seb no remote has
    whose change its base does not have and that `drop` does not name, a push no PR uses yet, or
    an open PR.
    """
    lines, force = {}, False
    for repo in local_branches(bundle):
        pushed = subprocess.run(
            [*GIT, "rev-parse", "--verify", "--quiet", get_remote_dev_ref(bundle, repo)],
            capture_output=True,
            check=False,
            cwd=get_repo_folder(repo),
        )
        committers = get_others_commits(bundle, repo, drop)
        if pushed.returncode and not committers:
            lines[repo] = "no commit of its own, never pushed"
            continue
        prs = get_branch_prs(repo, bundle)
        cherry = get_cherry(bundle, repo) if pushed.returncode and not prs else {}
        if not prs and not (cherry and all(cherry[sha] or sha in drop for sha in cherry)):
            raise Refused(
                f"{bundle} has commits in {repo} that no remote has"
                if pushed.returncode
                else f"{bundle} is pushed in {repo}, a PR may use it",
            )
        if opened := [pr for pr, state in prs.items() if state == "open"]:
            raise Refused(f"{bundle} has an open PR in {repo}: {', '.join(opened)}")
        base = get_base_for_repo(get_base_from_bundle_name(bundle), repo)
        lines[repo] = ", ".join(f"{pr} {state}" for pr, state in prs.items()) or (
            f"its commits are in {base}" if all(cherry.values()) else "never pushed"
        )
        if committers:
            force = True
            lines[repo] += f", commits of {', '.join(sorted(set(committers)))} that no remote has"
    if force:
        check_uncommitted(bundle)
    return lines, force


def add_repo(caller, query, body):
    """Add the worktree of a repo a bundle was made without. A container asks for its own only."""
    bundle = body.get("bundle") or ("" if caller == HOST else caller)
    if caller != HOST and bundle != caller:
        raise Refused("a bundle adds a repo to itself only")
    repo = body.get("repo") or ""
    if repo not in get_repos():
        raise ValueError(f"repo must be one of {', '.join(sorted(get_repos()))}")
    if not BUNDLE_NAME.fullmatch(bundle) or not is_made(get_worktree_bundle_folder(bundle)):
        raise ValueError(f"no bundle {bundle}")
    if os.path.exists(get_worktree_bundle_repo_folder(bundle, repo)):
        raise Refused(f"{repo} is already in {bundle}")

    def work(log):
        run_script(log, "add_repo.py", bundle, repo)
        return {"bundle": bundle, "repo": repo}

    return start_job(caller, "add-repo", work, bundle=bundle)


def delete(caller, query, body):
    """Hold the deletion of a bundle for Seb's yes in hok. A container asks for its own, or for
    the one of a bundle whose last task it queued."""
    bundle = body.get("bundle") or ("" if caller == HOST else caller)
    if not BUNDLE_NAME.fullmatch(bundle) or bundle in STICKY_BUNDLES:
        raise ValueError(f"not a bundle name: {bundle}")
    if caller not in (HOST, bundle, read_text(get_agent_folder(bundle) / "parent")):
        raise Refused("a bundle asks for the deletion of itself or of its children only")
    if HELD is None:
        raise Refused("no gateway-core beside useful-things: hok cannot list the deletion")
    folder = get_worktree_bundle_folder(bundle)
    dropped = get_dropped(bundle, body.get("drop") or [])
    drop = [sha for commits in dropped.values() for sha, _subject in commits]
    branches, _force = check_deletion(bundle, drop)
    if not branches and not os.path.isdir(folder):
        raise ValueError(f"no bundle {bundle}")
    with state_lock:
        queued = next((item for item in read_state()["queue"] if item["bundle"] == bundle), None)
    report = get_report(bundle) if get_window_pids(bundle) else None
    groups = [group["title"] for group in tab_groups.get_groups().get(bundle, [])]
    item = {
        "client": caller,
        "key": f"delete {bundle}",
        "lines": [
            f"the folder {folder}",
            *([f"the branch in {repo}: {line}" for repo, line in branches.items()] or ["no branch"]),
            *(
                f"its commit in {repo} that no remote has, lost: {sha[:12]} {subject}"
                for repo, commits in dropped.items()
                for sha, subject in commits
            ),
            "its container, and the databases named after it",
            *([f"its queued task, from {queued['parent']}"] if queued else []),
            *([f"its tab groups in Firefox: {', '.join(groups)}"] if groups else []),
            *(
                ["its agent window, which ends on this report:"]
                + [f"  {line}".rstrip() for line in report.splitlines()]
                if report
                else []
            ),
        ],
        "reason": str(body.get("reason") or ""),
        "send": {"bundle": bundle, **({"drop": drop} if drop else {})},
        "title": f"delete the bundle {bundle}",
        "yes": (
            "once its agent ended its turn, its window closes and its container stops, then"
            " delete_bundle deletes it"
        ),
    }
    return 200, {"bundle": bundle, "held": hold(item)}


def get_report(bundle):
    """The report of the verdict of a bundle's agent when its session ended on it: its agent
    window then shows nothing more to read."""
    agent = get_agent_folder(bundle)
    report, session = read_text(agent / "result.md"), read_text(agent / "session")
    transcript = session and get_transcript(session)
    if read_text(agent / "done") is None or not report or not transcript:
        return None
    return report if (get_last_text(transcript) or "").strip() == report else None


def close_window(bundle):
    """Close the agent window of a bundle, as Seb would: its claude and its container stop."""
    for pid in get_window_pids(bundle):
        try:
            os.kill(pid, signal.SIGUSR1)
        except ProcessLookupError:
            pass


def close_dead_window(bundle):
    """Close the agent windows a bundle or an ops session left on "Press Enter to close" once its
    claude ended, and wait for them to end: an ops window removes its agent folder as it ends, and
    close_window would close the new window too. A window whose agent runs stays."""
    if is_agent_running(bundle):
        return
    close_window(bundle)
    deadline = time.monotonic() + WINDOW_CLOSE_WAIT
    while get_window_pids(bundle):
        if time.monotonic() > deadline:
            raise Refused(f"{bundle}: the agent window where its claude ended stays open")
        time.sleep(0.5)


def get_terminals():
    """The agent-terminal.py process of each bundle, ops windows aside."""
    terminals = {}
    for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = path.read_bytes().split(b"\0")
        except OSError:
            continue
        if len(args) > 2 and args[1].endswith(b"/agent-terminal.py"):
            bundle = args[2].decode(errors="replace")
            if not bundle.startswith(OPS):
                terminals[bundle] = int(path.parent.name)
    return terminals


def read_stat(pid):
    """The parent of a process and when it started, in seconds since the epoch."""
    fields = pathlib.Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
    boot = next(
        line.split()[1] for line in pathlib.Path("/proc/stat").read_text().splitlines()
        if line.startswith("btime ")
    )
    return int(fields[1]), int(boot) + int(fields[19]) / os.sysconf("SC_CLK_TCK")


def is_minimized(pid, env):
    """Whether the windows of this process are all minimized, and it has one."""
    out = subprocess.run(["wmctrl", "-l", "-p"], capture_output=True, check=False, text=True, env=env)
    windows = [line.split()[0] for line in out.stdout.splitlines() if line.split()[2:3] == [str(pid)]]
    return bool(windows) and all(
        "_NET_WM_STATE_HIDDEN"
        in subprocess.run(
            ["xprop", "-id", window, "_NET_WM_STATE"],
            capture_output=True,
            check=False,
            text=True,
            env=env,
        ).stdout
        for window in windows
    )


def is_terminal_minimized(agent, env):
    """Whether the window of the terminal that holds the session of an agent is minimized."""
    try:
        parent, _started = read_stat(int(read_text(agent / "terminal")))
    except (OSError, IndexError, TypeError, ValueError):
        return False
    return is_minimized(parent, env)


def restart_old_terminal():
    """Open a new terminal on the agent of a bundle whose terminal started before the last change
    of TERMINAL_CODE, which it does not load again: the new one takes the session over and the old
    window closes. Only when its session is idle, as a background command a turn left (a PR watch)
    would end with its claude, nor the reset of the usage limit, that its claude goes on with by
    itself, and while its window is minimized, as Seb sees no window go and the new one opens
    minimized. One per call, each bundle once an hour at most."""
    changed = max(get_mtime(path) or 0 for path in TERMINAL_CODE)
    env = None
    for bundle, pid in get_terminals().items():
        agent = get_agent_folder(bundle)
        try:
            parent, started = read_stat(pid)
        except (OSError, IndexError, ValueError):
            continue
        if (
            started >= changed
            or time.time() - restarted.get(bundle, float("-inf")) < RESTART_RETRY
            or read_text(agent / "terminal") != str(pid)
            or (agent / "pause").exists()
            or is_deleting(bundle)
            or get_session_status(bundle, read_text(agent / "session")) != "idle"
            or TITLE.is_resuming(read_text(agent / "session"))
        ):
            continue
        env = env or session_env()
        if not is_minimized(parent, env):
            continue
        restarted[bundle] = time.time()
        print(f"{bundle}: its terminal restarts on the code of {format_time(changed)}", flush=True)
        takeover = f"restarts on the bundle-ctl code of {format_time(changed)}, same session"
        open_terminal(sys.stdout, bundle, takeover)
        return


def free_done_terminals():
    """Close the terminal of each bundle whose agent ended its run on a verdict, so that its
    container gives its memory back: the CI, the staging and the reviews of its PRs are for the
    next triage. Only once its session is idle on a last answer that opens on a verdict, for
    FREE_AFTER, and while its window is minimized, as for restart_old_terminal. A task queued for
    the bundle goes on in that session instead (can_go_on)."""
    with state_lock:
        state = read_state()
    queued = {item["bundle"] for item in state["queue"]} | set(state.get("deletions", []))
    env = None
    for bundle, pid in get_terminals().items():
        agent = get_agent_folder(bundle)
        session = read_text(agent / "session")
        transcript = session and get_transcript(session)
        if (
            bundle in queued
            or read_text(agent / "terminal") != str(pid)
            or not (agent / "done").exists()
            or (agent / "pause").exists()
            or is_deleting(bundle)
            or not transcript
            or time.time() - (get_mtime(transcript) or 0) < FREE_AFTER
            or get_session_status(bundle, session) != "idle"
            or TITLE.is_resuming(session)
        ):
            continue
        lines = (get_last_text(transcript) or "").strip().splitlines()
        if not lines or not cut.VERDICT.match(lines[0].strip().lstrip("#*_` ")):
            continue
        env = env or session_env()
        if not is_terminal_minimized(agent, env):
            continue
        print(f"{bundle}: its agent ended on its verdict, its terminal closes", flush=True)
        threading.Thread(target=close_freed, args=(bundle,), daemon=True).start()


def requeue_failed_terminals():
    """Put back in the queue the agent of each bundle whose session ended more turns in a row on an
    API error than its terminal tries again (TITLE.RETRIES), so that it frees its slot: the queue
    starts it again with GO_ON. Not after its verdict, as it holds no slot then, and only while its
    window is minimized, as for free_done_terminals."""
    with state_lock:
        state = read_state()
    queued = {item["bundle"] for item in state["queue"]} | set(state.get("deletions", []))
    env = None
    for bundle, pid in get_terminals().items():
        agent = get_agent_folder(bundle)
        session = read_text(agent / "session")
        if (
            bundle in queued
            or read_text(agent / "terminal") != str(pid)
            or (agent / "done").exists()
            or (agent / "pause").exists()
            or is_deleting(bundle)
            or not session
            or get_session_status(bundle, session) != "idle"
            or TITLE.count_api_errors(session) <= TITLE.RETRIES
        ):
            continue
        env = env or session_env()
        if not is_terminal_minimized(agent, env):
            continue
        print(
            f"{bundle}: {TITLE.RETRIES + 1} turns in a row ended on an API error,"
            " back to the queue",
            flush=True,
        )
        requeue_agent(HOST, {}, {"bundle": bundle})


def close_freed(bundle):
    """Close the agent window of a bundle as Seb would, its Firefox tab groups aside: its `pause`
    keeps them, as they hold the pages its report asks him to open."""
    pause = get_agent_folder(bundle) / "pause"
    write_text(pause, f"{time.time() + WINDOW_CLOSE_WAIT}\n")
    try:
        close_window(bundle)
        deadline = time.monotonic() + WINDOW_CLOSE_WAIT
        while get_window_pids(bundle) and time.monotonic() < deadline:
            time.sleep(0.5)
    finally:
        pause.unlink(missing_ok=True)


def get_terminal_wait(item, now):
    """What a task behind the agent of a terminal waits for before that terminal types it as the
    next prompt of the session, None when nothing: the verdict of that agent, then an idle session
    (no turn, his or a PR event's, no dialog, no wait for the usage limit). A task that names
    another session waits for the terminal to close."""
    agent = get_agent_folder(item["bundle"])
    session = read_text(agent / "session")
    if item.get("session") not in (None, session):
        # The `died` event of its last terminal comes back here.
        return "the terminal of the previous agent of the bundle to close"
    if not (agent / "done").exists():
        return get_previous_end(agent)
    if (item.get("after") or 0) > now:
        return f"its time, {format_time(item['after'])}"
    if (
        (agent / "pause").exists()
        or get_session_status(item["bundle"], session) != "idle"
        or TITLE.is_resuming(session)
    ):
        return "the previous agent of the bundle to be idle"
    return None


def get_previous_end(agent):
    """The wait of a task behind the agent at work in its bundle, with since when it works."""
    started = get_mtime(agent / "run.lock")
    since = f" (at work since {format_time(started)})" if started else ""
    return f"the previous agent of the bundle to end{since}"


def hand_to_terminal(item):
    """Leave the task to the terminal of the agent of its bundle, which types it as the next prompt
    of the same session and begins its run (agent-terminal.py): no window opens or closes. What
    that terminal and its PR watch use stays, and so do the model and the effort of the session,
    which claude keeps."""
    agent = get_agent_folder(item["bundle"])
    model, effort = (read_text(agent / name) for name in ("model", "effort"))
    write_task({**item, "resume": True, "model": model, "effort": effort}, keep=TERMINAL_FILES)
    print(f"{item['bundle']}: the task from {item['parent']} goes to its terminal", flush=True)


def clean(text):
    """Text of a caller as a card shows it: an escape sequence could redraw the card."""
    return "".join(char if char.isprintable() else f"\\x{ord(char):02x}" for char in text)


def hold(item):
    """Hold a request for Seb's yes in hok, the text of its caller cleaned: the id hok lists."""
    item = {**item, "lines": [clean(line) for line in item["lines"]]}
    if "reason" in item:
        item["reason"] = clean(item["reason"])
    return f"{HELD_LETTER}{HELD.add(HELD_DIR, item)}"


def hold_ops_task(caller, task):
    """Hold the task a bundle sends to a new ops agent for Seb's yes in hok. The same task asked
    again takes back the request that waits."""
    if HELD is None:
        raise Refused("no gateway-core beside useful-things: hok cannot list the task")
    item = {
        "client": caller,
        "key": f"ops {caller} {hashlib.sha256(task.encode()).hexdigest()}",
        "lines": task.splitlines(),
        "send": {"task": OPS_TASK.format(bundle=caller) + task},
        "title": "send a task to a new ops agent",
        "yes": "an ops agent window opens on the task below, with hctl and the drafts",
    }
    return 200, {"bundle": "ops", "held": hold(item)}


def may_queue(caller, bundle):
    """Whether a task of the caller goes to the queue of a bundle with no yes: from the host, the
    bundle itself, or the one that queued its last task."""
    return caller in (HOST, bundle, read_text(get_agent_folder(bundle) / "parent"))


def hold_task(caller, bundle, body, build=None):
    """Hold the task a bundle sends to a bundle it may not queue on for Seb's yes in hok: the agent
    of that bundle runs it in auto mode and pushes its branch. The same task asked again takes back
    the request that waits."""
    if HELD is None:
        raise Refused("no gateway-core beside useful-things: hok cannot list the task")
    task = OPS_TASK.format(bundle=caller) + body["task"]
    item = {
        "client": caller,
        "key": f"task {caller} {bundle} {hashlib.sha256(task.encode()).hexdigest()}",
        "lines": [
            *(["in its last session"] if body.get("resume") else []),
            *body["task"].splitlines(),
        ],
        "send": {"queue": {"body": {**body, "task": task}, "build": build, "bundle": bundle}},
        "title": f"send a task to the bundle {bundle}",
        "yes": f"the task below is queued for {bundle}, whose agent runs it and pushes its branch",
    }
    return 200, {"bundle": bundle, "held": hold(item)}


def find_cut():
    """The sessions a crash or a restart cut (cut.py), newest first: the last session of each
    bundle whose container is down, and each session of the ops container that no claude runs."""
    now = time.time()
    live = cut.get_live_sessions(
        [*pathlib.Path(CLAUDE_CONFIG_CONTAINER).glob("*/sessions"), PROJECTS.parent / "sessions"],
    )
    live |= {
        read_text(path.parent / "session")
        for path in pathlib.Path(get_worktree_container_folder()).glob("*/*/.agent/remote.json")
    }
    agents = {
        read_text(path): path.parent.parent.name
        for path in pathlib.Path(get_worktree_container_folder()).glob("*/*/.agent/session")
    }
    folders = [(PROJECTS / "-workspace", False)]
    if HOST_FOLDER:
        folders.append((PROJECTS / re.sub(r"[^A-Za-z0-9]", "-", CONFIG["REPO_ROOT"]), True))
    newest = {}
    for folder, ops in folders:
        for path in folder.glob("*.jsonl"):
            try:
                stopped = path.lstat().st_mtime
                if (
                    path.is_symlink()
                    or now - stopped > cut.WINDOW
                    or not cut.SESSION_ID.fullmatch(path.stem)
                ):
                    continue
                session = cut.read_session(path)
                name = f"{OPS}{path.stem}" if ops else agents.get(path.stem) or session["title"]
                if not ops and not (
                    BUNDLE_NAME.fullmatch(name or "")
                    and os.path.isdir(get_worktree_bundle_folder(name))
                ):
                    continue
            except (IndexError, OSError):
                continue
            if stopped > newest.get(name, (0,))[0]:
                newest[name] = (stopped, path, session)
    running = get_open_windows()
    rows = []
    for name, (stopped, path, session) in newest.items():
        why = cut.get_cut(session)
        if (
            not why
            or path.stem in live
            or (not name.startswith(OPS) and get_worktree_bundle_folder(name) in running)
        ):
            continue
        if None in (session["call"], session["prompt"], session["title"]):
            try:
                whole = cut.read_session(path, path.stat().st_size)
                session = {key: session[key] or whole[key] for key in session}
            except OSError:
                pass
        rows.append(
            {
                "call": session["call"],
                "name": name,
                "prompt": cut.shorten(session["prompt"] or ""),
                "session": path.stem,
                "stopped": stopped,
                "time": format_time(stopped),
                "title": session["title"] or "",
                "why": why,
            },
        )
    return sorted(rows, key=lambda row: row["stopped"], reverse=True)


def list_cut(caller, query, body):
    """The sessions a crash or a restart cut. Only the host and the ops container read them: the
    list shows the prompts of every bundle."""
    if caller != HOST:
        raise Refused("cut: only the host and the ops container list the cut sessions")
    return 200, {"days": cut.WINDOW // 86400, "sessions": find_cut()}


def hold_cut(caller, body):
    """Hold the resume of every cut session for one yes of Seb in hok. The same request asked
    again takes back the one that waits."""
    if body != {"cut": True, "resume": True}:
        raise ValueError("cut: only with resume, and no bundle nor task")
    if caller != HOST:
        raise Refused("cut: only the host and the ops container resume the cut sessions")
    if HELD is None:
        raise Refused("no gateway-core beside useful-things: hok cannot list the sessions")
    rows = find_cut()
    answer = {"days": cut.WINDOW // 86400, "sessions": rows}
    if not rows:
        return 200, answer
    lines = []
    for row in rows:
        named = row["title"] not in ("", row["name"])
        lines.append(row["name"] + (f": {row['title']}" if named else ""))
        lines.append(f"  cut {row['time']}: {row['why']}")
        lines += [f"  last call: {row['call']}"] if row["call"] else []
        lines.append(f"  last prompt: {row['prompt']}")
    lines += [
        "",
        f"What {rows[0]['name']} is told, the others with their own time and last call:",
        *cut.get_prompt(rows[0]).splitlines(),
    ]
    item = {
        "client": caller,
        "key": "resume the cut sessions",
        "lines": lines,
        "send": {
            "resume": [
                {"name": row["name"], "prompt": cut.get_prompt(row), "session": row["session"]}
                for row in rows
            ],
        },
        "title": f"resume {len(rows)} session{'s' * (len(rows) != 1)} a crash or a restart cut",
        "yes": (
            "each ops session goes on at once in an ops agent window, and each bundle session in"
            " its agent terminal through the queue, on the prompt below"
        ),
    }
    answer["held"] = hold(item)
    return 200, answer


def resume_ops(name, prompt):
    """Go on with a cut session of the ops container in an ops agent window, on this prompt, once
    the window where its claude ended closed."""
    check_ops_session(name, {"bundle": name, "terminal": True})
    close_dead_window(name)

    def work(log):
        get_agent_folder(name).mkdir(parents=True, exist_ok=True)
        (get_agent_folder(name) / "task.md").write_text(prompt)
        open_terminal(log, name)
        return {"bundle": name}

    return start_job(HOST, "open", work, ops_lock)


def resume_bundle(name, session, prompt):
    """Queue a cut session of a bundle to go on in its agent terminal, on this prompt, with the
    priority, the model and the effort of the last task of its agent, once the terminal where its
    claude ended closed."""
    body = {"priority": get_task_priority(name), "resume": True, "task": prompt}
    for key in ("effort", "model"):
        if value := read_text(get_agent_folder(name) / key):
            body[key] = value
    check_task(name, body, session)
    close_dead_window(name)
    return enqueue(HOST, name, body, session=session)


def resume_cut(sessions):
    """Seb's yes to the resume of the cut sessions. A session that runs again, or that a crash no
    longer leaves cut, is left as it is. The answer is what hok shows."""
    still = {row["session"] for row in find_cut()}
    opened, queued, left = [], [], []
    for wanted in sessions:
        name = wanted["name"]
        try:
            if wanted["session"] not in still:
                raise Refused("it runs again, or its turn ended since")
            if name.startswith(OPS):
                resume_ops(name, wanted["prompt"])
                opened.append(name)
            else:
                resume_bundle(name, wanted["session"], wanted["prompt"])
                queued.append(name)
        except (Refused, ValueError) as error:
            left.append(f"{name} ({error})")
    parts = [
        opened and f"ops agent windows open on {', '.join(opened)}",
        queued and f"queued to go on in their agent terminal: {', '.join(queued)}",
        left and f"left as they are: {', '.join(left)}",
    ]
    return "; ".join(filter(None, parts))


def get_held(caller, query, body):
    """What became of a request this caller held for Seb: it waits, or what hok said."""
    if HELD is None:
        raise Refused("no gateway-core beside useful-things")
    number = query.get("id", [""])[0].removeprefix(HELD_LETTER)
    if not number.isdigit():
        raise ValueError(f"not a request: {number}")
    deadline = time.monotonic() + min(int(query.get("wait", ["0"])[0]), JOB_WAIT)
    while True:
        answer = HELD.get_answer(HELD_DIR, number, caller)
        if answer is None:
            raise ValueError(f"no request {HELD_LETTER}{number} of yours")
        if answer["state"] != "waits" or time.monotonic() >= deadline:
            return 200, answer
        time.sleep(1)


def list_hok(caller, query, body):
    """The requests of hok that wait for the caller: a bundle its own, the host those of the ops
    session `?session=`. `hok waiting` of host-ctl lists them all, read again after HOK_KEPT
    seconds: every agent terminal asks."""
    if not HOK_WAITING.is_file():
        raise Refused("no host-ctl beside useful-things")
    session = query.get("session", [""])[0]
    if caller == HOST and not session:
        raise ValueError("the host names the ops session: ?session=")
    listed = read_hok_waiting()
    if caller == HOST:
        mine = [row for row in listed if (row["caller"], row["session"]) == ("ops", session)]
    else:
        mine = [row for row in listed if row["caller"] == caller]
    return 200, {"requests": mine}


def read_hok_waiting():
    """`hok waiting` of host-ctl, read again after HOK_KEPT seconds."""
    with hok_lock:
        if time.monotonic() - hok_read["at"] > HOK_KEPT:
            done = subprocess.run(
                [sys.executable, str(HOK_WAITING), "waiting"],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
            if done.returncode:
                raise Refused(f"hok waiting exited with {done.returncode}: {done.stderr[-300:]}")
            hok_read.update(at=time.monotonic(), listed=json.loads(done.stdout))
        return hok_read["listed"]


def get_in_hand():
    """The bundles Seb still has in hand: a hok request of the bundle waits, or a Firefox tab group
    of it is open, whose Agent and VS Code buttons need the bundle."""
    waiting = read_hok_waiting() if HOK_WAITING.is_file() else []
    return {row["caller"] for row in waiting} | set(tab_groups.get_groups())


hok_lock = threading.Lock()
hok_read = {"at": -HOK_KEPT, "listed": []}


def delete_stopped():
    """Start the deletions Seb said yes to whose container stopped: their jobs, by bundle."""
    with state_lock:
        state = read_state()
        if not state.get("deletions"):
            return {}
        running = get_open_windows()
        stopped = [
            bundle
            for bundle in state["deletions"]
            if get_worktree_bundle_folder(bundle) not in running
        ]
        state["deletions"] = [bundle for bundle in state["deletions"] if bundle not in stopped]
        drops = {bundle: state.get("drops", {}).pop(bundle, []) for bundle in stopped}
        write_state(state)
    started = {}
    for bundle in stopped:

        def work(log, bundle=bundle, drop=drops[bundle]):
            _branches, force = check_deletion(bundle, drop)
            keep_last_answer(bundle)
            run_script(log, "delete_bundle.py", *(["--force"] if force else []), bundle)
            with state_lock:
                state = read_state()
                state.get("builds", {}).pop(bundle, None)
                write_state(state)

        _code, answer = start_job(HOST, f"delete {bundle}", work, bundle=bundle)
        started[bundle] = jobs[answer["job"]]
    return started


def close_deleted():
    """Close the agent window of each bundle Seb said yes to delete, once no turn of its sessions
    runs, as he would: its container stops, which starts the deletion. A VS Code window is asked to
    close, and a container no window holds any more is stopped."""
    with state_lock:
        bundles = list(read_state().get("deletions", []))
    for bundle in bundles:
        if is_in_turn(bundle):
            continue
        if get_window_pids(bundle):
            close_window(bundle)
        elif not (container := get_bundle_container(bundle)):
            continue
        elif has_window(container):
            get_agent_folder(bundle).mkdir(exist_ok=True)
            write_text(get_agent_folder(bundle) / "close-window", "")
        else:
            stop_container(container)


def stop_container(container):
    subprocess.run(["podman", "stop", container], capture_output=True, check=False)


def is_in_turn(bundle):
    """Whether a claude of the bundle runs a turn, or waits for an answer in one, as the newest
    record of each session a claude runs says."""
    if remote_agent.is_remote(pathlib.Path(get_worktree_bundle_folder(bundle))):
        return True
    live = cut.get_live_sessions([pathlib.Path(CLAUDE_CONFIG_CONTAINER, bundle, "sessions")])
    return any(get_session_status(bundle, session) not in (None, "idle", "shell") for session in live)


def get_kept(bundle):
    """Where the last answer of a bundle's agent is kept once the bundle is deleted."""
    return STATE / "deleted" / f"{bundle}.md"


def keep_last_answer(bundle):
    """Copy the last answer of a bundle's agent out of the folder its deletion removes: the last
    text of its session, else the report its Stop hook wrote."""
    agent = get_agent_folder(bundle)
    session = read_text(agent / "session")
    transcript = session and get_transcript(session)
    answer = (transcript and get_last_text(transcript)) or read_text(agent / "result.md")
    if answer:
        get_kept(bundle).parent.mkdir(parents=True, exist_ok=True)
        get_kept(bundle).write_text(f"{answer.strip()}\n")


def get_last_use(bundle):
    """When the bundle was last made, given a task, opened in a window, or committed to."""
    folder = pathlib.Path(get_worktree_bundle_folder(bundle))
    paths = [
        folder / MADE,
        *(folder / ".agent").glob("*"),
        *pathlib.Path(CLAUDE_CONFIG_CONTAINER, bundle).glob("*"),
    ]
    for repo in get_repos():
        gitdir = read_text(folder / repo / ".git")
        if gitdir:
            paths.append(pathlib.Path(gitdir.removeprefix("gitdir: ")) / "logs" / "HEAD")
    return max(filter(None, map(get_mtime, paths)), default=0)


def get_closed_windows():
    """When the container of each bundle folder stopped, for the folders where none runs."""
    out = subprocess.run(
        [
            "podman",
            "ps",
            "--all",
            "--filter",
            "label=devcontainer.local_folder",
            "--format",
            '{{.State}} {{.ExitedAt}} {{index .Labels "devcontainer.local_folder"}}',
        ],
        capture_output=True,
        check=False,
        text=True,
    ).stdout
    closed, alive = {}, set()
    for line in out.splitlines():
        state, exited, folder = line.split(" ", 2)
        if state in ("exited", "stopped") and int(exited) > 0:
            closed[folder] = max(closed.get(folder, 0), int(exited))
        else:
            alive.add(folder)
    return {folder: exited for folder, exited in closed.items() if folder not in alive}


def get_unused_since(bundle, closed):
    """Since when nobody uses the bundle: its last use, or the stop of its container after it."""
    return max(get_last_use(bundle), closed.get(get_worktree_bundle_folder(bundle), 0))


def is_bare(folder):
    """Whether the folder holds an agent and the links of a bundle, and no worktree: its agent
    never ran `bctl build`."""
    return (
        (folder / ".agent").is_dir()
        and not is_made(folder)
        and {path.name for path in folder.iterdir()} <= BARE_FOLDER
    )


def is_parked(item):
    """Whether a queued task is the session of a bundle's agent that waits for Seb's resume or for
    a time of the day: its checkout may go meanwhile."""
    return bool(item.get("resume")) and bool(
        item.get("paused") or (item.get("after") or 0) > time.time(),
    )


def set_rebuild(bundle, session):
    """Let the parked session of a bundle go on bare once its checkout is deleted, and fetch it
    again when its agent asks: False when it is no longer parked. With no session, take that back,
    as the bundle is kept."""
    with state_lock:
        state = read_state()
        item = next((item for item in state["queue"] if item["bundle"] == bundle), None)
        if item and session and is_parked(item):
            item["build"] = ["fetch_bundle.py", bundle, "--no-open"]
            item.setdefault("session", session)
        elif item and not session:
            item.pop("build", None)
        else:
            return False
        write_state(state)
    return True


def drop_idle_reviews():
    """Delete the bundles of the PRs of others that nobody used for REVIEW_CLOSED since their
    container stopped, or for REVIEW_IDLE when none ever ran: `bctl fetch` brings one back. One
    that holds work is kept until it is used again, but not one that only holds the older head of
    a PR its author rebased since. One Seb still has in hand is kept (get_in_hand).
    """
    closed = get_closed_windows()
    open_windows = get_open_windows()
    state = read_state()
    queue = state.get("queue", [])
    parked = {item["bundle"] for item in queue if is_parked(item)}
    busy = ({item["bundle"] for item in queue} - parked) | set(state.get("building", []))
    busy |= {
        job["verb"].removeprefix("delete ")
        for job in jobs.values()
        if job["verb"].startswith("delete ") and job["state"] in ("waiting", "running")
    }
    started, in_hand = {}, None
    for folder in sorted(pathlib.Path(get_worktree_container_folder()).glob("*/*")):
        bundle = folder.name
        bare = is_bare(folder)
        if (
            (BUNDLE_SUFFIX in bundle and not bare)
            or bundle in STICKY_BUNDLES
            or bundle in busy
            or (bare and bundle in parked)
            or not BUNDLE_NAME.fullmatch(bundle)
            or not (bare or is_made(folder))
            or str(folder) in open_windows
            or get_worktree_bundle_folder(bundle) != str(folder)
        ):
            continue
        since = get_unused_since(bundle, closed)
        idle = REVIEW_CLOSED if str(folder) in closed else REVIEW_IDLE
        if time.time() - since < idle or kept_reviews.get(bundle) == since:
            continue
        if in_hand is None:
            in_hand = get_in_hand()
        if bundle in in_hand:
            continue

        def work(log, bundle=bundle, since=since, bare=bare, parked=bundle in parked):
            if (
                get_worktree_bundle_folder(bundle) in get_open_windows()
                or get_unused_since(bundle, get_closed_windows()) != since
            ):
                log.write(f"{bundle} was used since {format_time(since)}\n")
                return {}
            if parked and not set_rebuild(bundle, read_text(get_agent_folder(bundle) / "session")):
                log.write(f"{bundle} is kept: its session left the queue\n")
                return {}
            if bare:
                if not is_bare(pathlib.Path(get_worktree_bundle_folder(bundle))):
                    log.write(f"{bundle} was built since\n")
                    return {}
                shutil.rmtree(get_worktree_bundle_folder(bundle))
                with state_lock:
                    state = read_state()
                    state.get("builds", {}).pop(bundle, None)
                    write_state(state)
                return {}
            try:
                force = any([get_others_commits(bundle, repo) for repo in local_branches(bundle)])
                if force:
                    check_uncommitted(bundle)
                run_script(log, "delete_bundle.py", *(["--force"] if force else []), bundle)
            except Refused:
                kept_reviews[bundle] = since
                if parked:
                    set_rebuild(bundle, None)
                raise

        print(f"delete {bundle}: unused since {format_time(since)}", flush=True)
        _code, answer = start_job(HOST, f"delete {bundle}", work, bundle=bundle)
        started[bundle] = jobs[answer["job"]]
    return started


def send_deletion(item):
    """Seb's yes to a deletion: it runs now when the container of the bundle stopped, else at its
    stop. The answer is what hok shows him.
    """
    bundle, drop = item["send"]["bundle"], item["send"].get("drop", [])
    try:
        check_deletion(bundle, drop)
    except Refused as error:
        raise ValueError(str(error)) from None
    with state_lock:
        state = read_state()
        deletions = state.setdefault("deletions", [])
        if bundle not in deletions:
            deletions.append(bundle)
        if drop:
            state.setdefault("drops", {})[bundle] = drop
        else:
            state.get("drops", {}).pop(bundle, None)
        state["queue"] = [queued for queued in state["queue"] if queued["bundle"] != bundle]
        write_state(state)
    tab_groups.ask({"op": "close", "bundle": bundle})
    close_deleted()
    has_agent = (get_agent_folder(bundle) / "session").exists()
    kept = f", its last answer is kept in {get_kept(bundle)}" if has_agent else ""
    job = delete_stopped().get(bundle)
    if job is None:
        return (
            f"{bundle} is deleted once its agent ended its turn: its window closes and its"
            f" container stops then{kept}"
        )
    wait_job(job)
    if job["state"] == "done":
        return f"{bundle} deleted{kept}"
    return f"{bundle} is being deleted, see {job['log']}"


def send_task(item):
    """Seb's yes to the task a bundle sends to another one: queued as from the parent of that
    bundle, so that the yes does not make the sender its parent."""
    queued = item["send"]["queue"]
    bundle, body = queued["bundle"], queued["body"]
    try:
        check_task(bundle, body)
    except Refused as error:
        raise ValueError(str(error)) from None
    parent = read_text(get_agent_folder(bundle) / "parent") or HOST
    enqueue(parent, bundle, body, queued["build"])
    return f"the task of {item['client']} is queued for {bundle}"


def send_held(item):
    """Seb's yes to what was held: the deletion of a bundle, the task a bundle sends to a new ops
    agent or to another bundle, or the resume of the cut sessions."""
    if "resume" in item["send"]:
        return resume_cut(item["send"]["resume"])
    if "queue" in item["send"]:
        return send_task(item)
    if "node_modules" in item["send"]:
        base, package = (item["send"]["node_modules"][key] for key in ("base", "package"))
        _code, answer = start_job(
            HOST,
            "node-modules",
            lambda log: refresh_node_modules(log, base, package),
        )
        job = jobs[answer["job"]]
        wait_job(job)
        if job["state"] == "done":
            return f"the node_modules of {base} is built: the bundles on its package.json load it"
        return f"the node_modules of {base} is being built, see {job['log']}"
    if "bundle" in item["send"]:
        return send_deletion(item)
    _code, answer = open_ops(HOST, "ops", {"task": item["send"]["task"]})
    wait_job(jobs[answer["job"]])
    return f"an ops agent window opens on the task of {item['client']}"


def wait_job(job):
    """Wait a while for a job a yes started: a failure is what hok shows."""
    deadline = time.monotonic() + DELETE_WAIT
    while job["state"] in ("waiting", "running") and time.monotonic() < deadline:
        time.sleep(0.5)
    if job["state"] == "failed":
        raise ValueError(f"{job['result']['error']}, see {job['log']}")


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
    if "task" not in body:
        check_disk()
    folder = get_worktree_bundle_folder(bundle)
    if is_made(folder) or (not os.path.exists(folder) and local_branches(bundle)):
        raise Refused(f"{bundle} already exists")
    check_unpushed(bundle, "gnb")
    check_task(bundle, body)
    args = [base, name, "--no-push", "--no-open"]
    for repo in branch_repos:
        args += ["--branch-repo", repo]
    if "task" in body and get_agent_folder(bundle).is_dir() and not may_queue(caller, bundle):
        return hold_task(caller, bundle, body, ["create_bundle.py", *args])
    if "task" in body:
        return 202, enqueue(caller, bundle, body, ["create_bundle.py", *args])

    def work(log):
        return build(log, caller, bundle, body, "create_bundle.py", *args)

    return start_job(caller, "create", work, bundle=bundle)


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
    if "task" not in body:
        check_disk()
    if is_made(get_worktree_bundle_folder(bundle)):
        raise Refused(f"{bundle} is already here, open it instead")
    check_unpushed(bundle, "pfb")
    check_task(bundle, body)
    if "task" in body and get_agent_folder(bundle).is_dir() and not may_queue(caller, bundle):
        return hold_task(caller, bundle, body, ["fetch_bundle.py", name, "--no-open"])
    if "task" in body:
        return 202, enqueue(caller, bundle, body, ["fetch_bundle.py", name, "--no-open"])

    def work(log):
        return build(log, caller, bundle, body, "fetch_bundle.py", name, "--no-open")

    return start_job(caller, "fetch", work, bundle=bundle)


def read_package(base):
    """The package.json of the web tooling of odoo on the base branch."""
    ref = f"{get_remote_ref(base, 'odoo')}:addons/web/tooling/_package.json"
    res = subprocess.run(
        [*GIT, "-C", get_repo_folder("odoo"), "show", ref],
        capture_output=True,
        check=False,
        text=True,
    )
    if res.returncode:
        raise Refused(f"no package.json of the web tooling at {ref}: {res.stderr.strip()}")
    return res.stdout


def node_modules_tree(package):
    """The folder of the store of node_modules (scripts/README.md) built from `package`."""
    return pathlib.Path(WORKTREE_CONTAINER) / ".node_modules" / node_modules_key(package.encode())


def refresh_node_modules(log, base, package):
    """npm installs `package` in a folder of its own, in HOST_BOX with the network, which then
    goes in the store under the key of `package`. A tree in the store never changes: a bundle
    linked to it loads it at once, and the others keep theirs. Refused with no HOST_BOX: npm runs
    the scripts of the packages."""
    tree = node_modules_tree(package)
    if (tree / "node_modules" / ".bin").is_dir():
        return {"base": base, "tree": tree.name}
    if not HOST_BOX:
        raise Refused("no HOST_BOX in config.env: npm would run the package scripts unboxed")
    tree.parent.mkdir(exist_ok=True)
    fresh = pathlib.Path(tempfile.mkdtemp(prefix=".node_modules.", dir=WORKTREE_CONTAINER))
    try:
        (fresh / "package.json").write_text(package)
        install = [HOST_BOX, "--net", "--dir", str(fresh), "--"]
        install += ["npm", "install", "--no-audit", "--no-fund"]
        log.write(f"$ {' '.join(install)}\n")
        log.flush()
        res = subprocess.run(
            install,
            cwd=fresh,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if res.returncode or not (fresh / "node_modules" / ".bin").is_dir():
            raise Refused(f"npm install exited with {res.returncode}")
        if not tree.is_dir():
            fresh.rename(tree)
    finally:
        shutil.rmtree(fresh, ignore_errors=True)
    return {"base": base, "tree": tree.name}


def build_node_modules():
    """Build the tree of the package.json of each base whose odoo head moved, when the store has
    none: a bundle fetches the bases in the shared .git, and the host builds what `bctl
    node-modules` would build for it (Seb, 2026-10-27)."""
    refs = [get_remote_ref(base, "odoo") for base in STICKY_BUNDLES]
    heads = read_base_heads("odoo", refs)
    for base, ref in zip(STICKY_BUNDLES, refs):
        if ref not in heads or package_heads.get(base) == heads[ref]:
            continue
        package_heads[base] = heads[ref]
        package = read_package(base)
        if not (node_modules_tree(package) / "node_modules" / ".bin").is_dir():
            start_job(
                HOST,
                "node-modules",
                lambda log, base=base, package=package: refresh_node_modules(log, base, package),
            )


def build_node_modules_safely():
    try:
        build_node_modules()
    except Exception:
        traceback.print_exc()


def node_modules(caller, query, body):
    """Build the node_modules of the package.json of a base's branch, when the store has none:
    at once for the host, held for Seb's yes for a bundle, which asks for its own base only."""
    base = body.get("base") or ("" if caller == HOST else get_base_from_bundle_name(caller))
    if base not in STICKY_BUNDLES:
        raise ValueError(f"base must be one of {', '.join(STICKY_BUNDLES)}")
    if caller != HOST and base != get_base_from_bundle_name(caller):
        raise Refused("a bundle asks for the node_modules of its own base only")
    package = read_package(base)
    tree = node_modules_tree(package)
    if (tree / "node_modules" / ".bin").is_dir():
        return 200, {"base": base, "tree": tree.name}
    if caller == HOST:
        return start_job(
            caller,
            "node-modules",
            lambda log: refresh_node_modules(log, base, package),
        )
    if HELD is None:
        raise Refused("no gateway-core beside useful-things: hok cannot list the request")
    try:
        link = os.readlink(f"{get_worktree_bundle_repo_folder(caller, 'odoo')}/node_modules")
    except OSError:
        link = ""
    linked = re.fullmatch(r"/opt/node_modules/([0-9a-f]{16})/node_modules", link)
    used = linked and read_text(tree.parent / linked[1] / "package.json")
    diff = used and list(difflib.unified_diff(used.splitlines(), package.splitlines(), lineterm=""))
    item = {
        "client": caller,
        "key": f"node-modules {base}",
        "lines": diff[2:] if diff else ["no tree in use yet:", *package.splitlines()],
        "reason": str(body.get("reason") or ""),
        "send": {"node_modules": {"base": base, "package": package}},
        "title": f"build the node_modules of the package.json of {base}",
        "yes": (
            "npm installs this package.json in a box, with the network: the bundles on it load"
            " the new node_modules at once"
        ),
    }
    return 200, {"base": base, "held": hold(item)}


def prepare_folder(bundle):
    UtilsRunner().prepare_worktree_bundle_folder(bundle_name=bundle)


def build_bundle(caller, query, body):
    """Make the bundle whose agent started in its bare folder, with the script its task was queued
    with: only that bundle or the host may ask it."""
    bundle = body.get("bundle") or caller
    if caller not in (HOST, bundle):
        raise Refused("build: a container builds its own bundle only")
    with state_lock:
        args = read_state().get("builds", {}).get(bundle)
    if not args:
        raise Refused(f"{bundle}: nothing to build, its bundle is made or was never queued")
    check_disk()
    check_unpushed(bundle, "bctl build")

    def work(log):
        set_building(bundle, True)
        try:
            run_script(log, *args)
        finally:
            set_building(bundle, False)
        with state_lock:
            state = read_state()
            state.get("builds", {}).pop(bundle, None)
            write_state(state)
        return {"bundle": bundle}

    return start_job(caller, "build", work, bundle=bundle)


def check_ops_session(bundle, body):
    """Why a session of the ops container cannot move to an ops agent window, or without
    `terminal` to a tab of the ops window."""
    session = bundle.removeprefix(OPS)
    bodies = ({"bundle": bundle}, {"bundle": bundle, "terminal": True})
    if not SESSION_ID.fullmatch(session) or body not in bodies:
        raise ValueError(f"{OPS}SESSION: a session id, alone or with terminal")
    if not get_transcript(session):
        raise Refused(f"{bundle}: no conversation to go on with")
    if body.get("terminal") and is_agent_running(bundle):
        raise Refused(f"{bundle}: already in a window")


def move_to_tab(log, bundle):
    """Close the ops agent window of a session, and leave the session to the ops-window extension,
    which opens it in a tab of the ops window. The storage of the Claude extension is the one
    folder of the ops container that the host writes too."""
    log.write(f"moving {bundle} to a tab of the ops window\n")
    log.flush()
    close_window(bundle)
    deadline = time.monotonic() + WINDOW_CLOSE_WAIT
    while is_agent_running(bundle):
        if time.monotonic() > deadline:
            raise Refused(f"{bundle}: its agent window still runs the session")
        time.sleep(0.5)
    session = bundle.removeprefix(OPS)
    mode, cut = get_resume(get_transcript(session))
    storage = pathlib.Path(CLAUDE_CONFIG_CONTAINER) / os.path.basename(HOST_FOLDER)
    storage /= "extension-storage"
    if mode:
        (storage / "session-permission-modes").mkdir(parents=True, exist_ok=True)
        seed = {"mode": mode, "updatedAt": int(time.time() * 1000)}
        (storage / "session-permission-modes" / f"{session}.json").write_text(json.dumps(seed))
    (storage / OPS_OPEN).mkdir(parents=True, exist_ok=True)
    (storage / OPS_OPEN / session).write_text(GO_ON if cut else "")


def open_ops(caller, bundle, body):
    """The ops window, or with a task or `terminal` a new ops agent window, at once: no cap and no
    queue, as for a tab of the ops window. ops-SESSION with `terminal` goes on with the session of
    a tab in an ops agent window, and alone with the session of an ops agent window in a tab. A
    bundle may only send a task, which waits for Seb's yes in hok, as it would run where hctl is."""
    task = body.get("task")
    if "task" in body and (not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK):
        raise ValueError(f"task: a text of at most {MAX_TASK} characters")
    if caller != HOST and HOST_FOLDER and body == {"bundle": "ops", "task": task}:
        return hold_ops_task(caller, task)
    if caller != HOST or not HOST_FOLDER:
        raise Refused("ops: only the host and the ops container open it, a bundle sends a task")
    if bundle != "ops":
        check_ops_session(bundle, body)
    elif body.get("resume") or {"after", "effort", "model", "paused", "priority"} & set(body):
        raise ValueError("ops: only a task or terminal")
    name = bundle
    if bundle == "ops" and ("task" in body or body.get("terminal")):
        name = f"{OPS}{uuid.uuid4()}"

    def work(log):
        if name == "ops":
            open_window(log, name)
            return {"bundle": name}
        if bundle != "ops" and not body.get("terminal"):
            move_to_tab(log, name)
            open_window(log, "ops")
            return {"bundle": name}
        if task:
            get_agent_folder(name).mkdir(parents=True)
            (get_agent_folder(name) / "task.md").write_text(task)
        open_terminal(log, name)
        return {"bundle": name}

    return start_job(caller, "open", work, ops_lock)


def open_bundle(caller, query, body):
    bundle = body.get("bundle") or ""
    if "cut" in body:
        return hold_cut(caller, body)
    if bundle == "ops" or bundle.startswith(OPS):
        return open_ops(caller, bundle, body)
    if not BUNDLE_NAME.fullmatch(bundle) or not os.path.isdir(get_worktree_bundle_folder(bundle)):
        raise ValueError(f"no bundle folder for {bundle}")
    folder = get_worktree_bundle_folder(bundle)
    goes_on = body.get("resume") or body.get("terminal")
    if not is_made(folder) and not (goes_on and is_bare(pathlib.Path(folder))):
        raise Refused(f"{bundle} is half-made (its create or fetch failed): do it again")
    if not goes_on and "task" not in body and remote_agent.is_remote(pathlib.Path(folder)):
        raise Refused(f"its agent runs on a worker: `bctl bring-back {bundle}` first")
    if body.get("terminal"):
        check_terminal(caller, bundle, body)

        def work(log):
            agent = get_agent_folder(bundle)
            if not (agent / "session").exists():
                agent.mkdir(exist_ok=True)
                write_text(agent / "session", f"{uuid.uuid4()}\n")
                write_text(agent / "task.md", "")
            open_terminal(log, bundle)
            return {"bundle": bundle}

        return start_job(caller, "open", work, lock=None, bundle=bundle)
    check_task(bundle, body)
    if "task" in body and not may_queue(caller, bundle):
        return hold_task(caller, bundle, body)
    if "task" in body:
        return 202, enqueue(caller, bundle, body)
    return start_job(
        caller,
        "open",
        lambda log: open_or_enqueue(log, caller, bundle, body),
        bundle=bundle,
    )


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
    ("POST", "/add-repo"): add_repo,
    ("GET", "/branches"): branches,
    ("GET", "/cut"): list_cut,
    ("GET", "/held"): get_held,
    ("GET", "/hok"): list_hok,
    ("GET", "/job"): get_job,
    ("GET", "/queue"): get_queue,
    ("GET", "/runs"): list_runs,
    ("GET", "/status"): status,
    ("GET", "/whoami"): whoami,
    ("POST", "/build"): build_bundle,
    ("POST", "/close"): close_agent_window,
    ("POST", "/create"): create,
    ("POST", "/delete"): delete,
    ("POST", "/fetch"): fetch,
    ("POST", "/force-queue"): force_queue,
    ("POST", "/move"): move_task,
    ("POST", "/node-modules"): node_modules,
    ("POST", "/open"): open_bundle,
    ("POST", "/requeue"): requeue_agent,
    ("POST", "/bring-back"): bring_back,
    ("POST", "/schedule"): schedule_task,
    ("POST", "/unqueue"): unqueue,
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
        elif (url.path == "/run" or url.path.startswith("/worker/")) and is_standin(self.request):
            code, answer = 403, {"error": "a remote agent runs on its worker already"}
        elif method == "POST" and url.path.startswith("/worker/"):
            code, answer = self.relay(url)
            if code is None:
                return
        elif length > MAX_BODY:
            code, answer = 413, {"error": f"body over {MAX_BODY} bytes"}
        elif (method, url.path) == ("POST", "/run"):
            try:
                body = json.loads(self.rfile.read(length)) if length else {}
            except ValueError:
                body = {}
            body = body if type(body) is dict else {}
            print(f"{self.caller} POST /run {body.get('kind', '')}".strip(), flush=True)
            router = WORKERS.choose if WORKERS.names and is_in_container(self.request) else None
            runs.serve(RUNS, self, self.caller, body, router)
            return
        elif verb is None:
            code, answer = 404, {"error": f"no verb {method} {url.path}"}
        elif (method, url.path) in HOST_ONLY and is_in_container(self.request):
            code, answer = 403, {"error": "only from the host, a container cannot ask it"}
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

    def relay(self, url):
        """`POST /worker/VERB?worker=NAME&arg=...`: the stream of a bundle to a remote worker
        (README.md, "Remote runs"). Returns (None, None) once the relay answered.
        """
        if not is_in_container(self.request) or self.caller == "host":
            return 403, {"error": "only a bundle sends work to a worker"}
        query = urllib.parse.parse_qs(url.query)
        name = (query.get("worker") or [""])[0]
        verb = url.path.removeprefix("/worker/")
        try:
            code = WORKERS.relay(self, self.caller, name, verb, query.get("arg", []))
        except workers.Refused as error:
            return 409, {"error": str(error)}
        print(f"{self.caller} POST /worker/{verb} {name} ssh={code}", flush=True)
        return None, None


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def check_box():
    """Refuse to serve when the git of PATH is not the one host-ctl renders, which runs git in
    HOST_BOX for the bundle repos: a bundle writes their .git/config (host-ctl README)."""
    git = shutil.which("git")
    try:
        with open(git, "rb") as file:
            boxed = bool(HOST_BOX) and HOST_BOX.encode() in file.read(1 << 16)
    except (OSError, TypeError):
        boxed = False
    if not boxed:
        sys.exit(f"bundle-ctl: the git of PATH ({git}) does not run HOST_BOX ({HOST_BOX})")


def main():
    check_box()
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    os.umask(0o077)
    server = Server(str(SOCKET), Handler)
    with state_lock:
        state = read_state()
        state["building"] = []
        state["launches"] = get_alive_launches(state["launches"])
        write_state(state)
    print(f"listening on {SOCKET}", flush=True)
    RUNS.watch()
    if WORKERS.names:
        threading.Thread(target=WORKERS.keep_polling, daemon=True).start()
    if HELD:
        threading.Thread(
            target=HELD.serve,
            args=(HELD_DIR, send_held),
            daemon=True,
        ).start()
    keep_launching()
    server.serve_forever()


if __name__ == "__main__":
    main()
