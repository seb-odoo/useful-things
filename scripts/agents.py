"""The agent state bundle-ctl keeps: its queue in state.json, and the `.agent/` files of each bundle.

Read by the bundle-ctl daemon and by branch_status.py.
"""

import json
import pathlib
import re
import subprocess
import time

from commands import get_worktree_bundle_folder
from config import STATE_ROOT

IDLE_AFTER = 15 * 60
LAUNCH_GRACE = 15 * 60
SESSION_ID = re.compile(r"[0-9a-f-]{36}")
SESSIONS = pathlib.Path.home() / ".claude" / "projects" / "-workspace"
STATE = pathlib.Path(STATE_ROOT) / "bundle-ctl"
STATE_FILE = STATE / "state.json"

title_by_session = {}


def read_text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def get_agent_folder(bundle):
    return pathlib.Path(get_worktree_bundle_folder(bundle)) / ".agent"


def get_bundle_container(bundle):
    """The id of the bundle's running container, None when it has none."""
    folder = get_worktree_bundle_folder(bundle)
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
