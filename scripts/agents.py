"""The agent state bundle-ctl keeps: its queue in state.json, and the `.agent/` files of each bundle.

Read by the bundle-ctl daemon and by branch_status.py.
"""

import importlib.util
import json
import os
import pathlib
import re
import subprocess
import time

from commands import get_worktree_bundle_folder
from config import STATE_ROOT

IDLE_AFTER = 15 * 60
LAUNCH_GRACE = 15 * 60
OPS = "ops-"
PROJECTS = pathlib.Path.home() / ".claude" / "projects"
SESSION_ID = re.compile(r"[0-9a-f-]{36}")
SESSIONS = pathlib.Path.home() / ".claude" / "projects" / "-workspace"
STATE = pathlib.Path(STATE_ROOT) / "bundle-ctl"
STATE_FILE = STATE / "state.json"
TAKEOVER = pathlib.Path(__file__).resolve().parent.parent / "bundle-ctl/client/agent-takeover.py"

title_by_session = {}


def read_text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def get_agent_folder(bundle):
    """The `.agent/` folder of a bundle, or the folder of an ops agent window (ops-SESSION)."""
    if bundle.startswith(OPS):
        return STATE / "ops" / bundle.removeprefix(OPS)
    return pathlib.Path(get_worktree_bundle_folder(bundle)) / ".agent"


def get_transcript(session):
    """The transcript of a Claude session of any container, None when it has none."""
    return next(PROJECTS.glob(f"*/{session}.jsonl"), None)


def load_takeover():
    spec = importlib.util.spec_from_file_location("takeover", TAKEOVER)
    takeover = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(takeover)
    return takeover


def get_resume(transcript):
    """The permission mode a session last ran in, and whether its turn was cut: its last message
    is a prompt, or a tool call with no result."""
    takeover = load_takeover()
    author, kinds = takeover.get_last_message(takeover.read_entries(transcript))
    return takeover.get_mode(transcript), author == "user" or "tool_use" in kinds


def get_last_text(transcript):
    """The last message of a session when it is a text of the assistant, thinking aside: None
    when a prompt, a tool call or its result came after."""
    for entry in reversed(load_takeover().read_entries(transcript)):
        if entry.get("type") not in ("assistant", "user") or entry.get("isSidechain"):
            continue
        content = entry.get("message", {}).get("content")
        kinds = {block.get("type") for block in content} if isinstance(content, list) else set()
        if kinds == {"thinking"}:
            continue
        if entry["type"] != "assistant" or kinds != {"text"}:
            return None
        return "".join(block["text"] for block in content)
    return None


def is_agent_running(bundle):
    """Whether the agent of a bundle or an ops window runs: agent-terminal.py BUNDLE. The
    agent-terminator.py of the window holds the same words as COMMAND, and does not count."""
    for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = path.read_bytes().split(b"\0")
        except OSError:
            continue
        if bundle.encode() not in args:
            continue
        if any(arg.endswith(b"agent-terminator.py") for arg in args):
            continue
        if any(arg.endswith(b"agent-terminal.py") for arg in args):
            return True
    return False


def get_window_pids(bundle):
    """The agent-terminator.py processes that hold an agent window of a bundle or an ops window."""
    pids = []
    for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = path.read_bytes().split(b"\0")
        except OSError:
            continue
        if bundle.encode() in args and any(arg.endswith(b"agent-terminator.py") for arg in args):
            pids.append(int(path.parent.name))
    return pids


def is_handled(bundle, news):
    """Whether Seb opened the bundle in VS Code since its last agent task, and nothing came since.

    The first call after his click keeps `news`, what waits on him then: a later one with an item
    it lacks is something new.
    """
    folder = get_agent_folder(bundle)
    opened = get_mtime(folder / "handled") or 0
    if opened <= (get_mtime(folder / "task.md") or 0):
        return False
    seen = folder / "handled.json"
    if (get_mtime(seen) or 0) < opened:
        written = folder / f"handled.json.{os.getpid()}"
        written.write_text(json.dumps(news))
        written.replace(seen)
        return True
    try:
        return not set(news) - set(json.loads(seen.read_text()))
    except (OSError, ValueError):
        return False


def mark_handled(bundle):
    folder = get_agent_folder(bundle)
    folder.mkdir(exist_ok=True)
    (folder / "handled").touch()


def get_bundle_container(bundle):
    """The id of the bundle's running container, None when it has none."""
    return get_container(get_worktree_bundle_folder(bundle))


def get_container(folder):
    """The id of the running container of a folder, None when it has none."""
    out = subprocess.run(
        ["podman", "ps", "--quiet", "--filter", f"label=devcontainer.local_folder={folder}"],
        capture_output=True,
        check=False,
        text=True,
    ).stdout.split()
    return out[0] if out else None


def has_window(container):
    """Whether a VS Code window is attached to the container: its server then runs in it."""
    out = subprocess.run(
        ["podman", "top", container, "args"],
        capture_output=True,
        check=False,
        text=True,
    ).stdout
    return "server-main.js" in out


def get_mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def format_time(stamp):
    today = time.strftime("%F") == time.strftime("%F", time.localtime(stamp))
    return time.strftime("%H:%M" if today else "%m-%d %H:%M", time.localtime(stamp))


def get_session_title(path):
    if path not in title_by_session:
        try:
            with path.open() as file:
                title_by_session[path] = json.loads(file.readline()).get("customTitle")
        except (OSError, ValueError):
            return None
    return title_by_session[path]


def get_activity(bundle, session):
    paths = [path for path in SESSIONS.glob("*.jsonl") if get_session_title(path) == bundle]
    if SESSION_ID.fullmatch(session or ""):
        paths.append(SESSIONS / f"{session}.jsonl")
    active = max(filter(None, map(get_mtime, paths)), default=None)
    if not active:
        return None, False
    age = time.time() - active
    if age > IDLE_AFTER:
        return f"idle since {format_time(active)}", True
    return f"active {max(1, int(age // 60))} min ago", False


def get_agent(bundle, head_date=0, alive=True):
    folder = get_agent_folder(bundle)
    if not (folder / "task.md").is_file():
        return None
    done = read_text(folder / "done")
    ended = get_mtime(folder / ("waiting" if done is None else "done"))
    if done is not None:
        state = "done"
    elif not alive:
        state = "stopped"
    elif ended is not None:
        state = "waiting"
    else:
        state = "running"
    activity, idle = get_activity(bundle, read_text(folder / "session"))
    queued = read_text(folder / "queued")
    started = get_mtime(folder / "run.lock")
    return {
        "activity": activity,
        "done": done,
        "effort": read_text(folder / "effort"),
        "ended": ended and format_time(ended),
        "idle": idle,
        "model": read_text(folder / "model"),
        "parent": read_text(folder / "parent"),
        "queued": queued and format_time(float(queued)),
        "result": (read_text(folder / "result.md") or "")[:500],
        "retry": read_text(folder / "retry"),
        "stale": state == "done" and head_date > (ended or time.time()),
        "started": started and format_time(started),
        "state": state,
    }


def read_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"launches": {}, "queue": []}
