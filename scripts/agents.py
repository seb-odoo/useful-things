"""The agent state bundle-ctl keeps: its queue in state.json, and the `.agent/` files of each bundle.

Read by the bundle-ctl daemon and by branch_status.py.
"""

import errno
import importlib.util
import json
import os
import pathlib
import re
import stat
import subprocess
import time

from commands import BUNDLE_NAME, get_worktree_bundle_folder
from config import CLAUDE_CONFIG_CONTAINER, STATE_ROOT

EFFORTS = ("low", "medium", "high", "xhigh")
HANDLED_FOR = 24 * 3600
IDLE_AFTER = 15 * 60
LAUNCH_GRACE = 15 * 60
MAX_VALUE = 128
MODELS = ("haiku", "opus", "sonnet")
OPS = "ops-"
PROJECTS = pathlib.Path.home() / ".claude" / "projects"
SESSION_ID = re.compile(r"[0-9a-f-]{36}")
SESSIONS = pathlib.Path.home() / ".claude" / "projects" / "-workspace"
STATE = pathlib.Path(STATE_ROOT) / "bundle-ctl"
STATE_FILE = STATE / "state.json"
TAKEOVER = pathlib.Path(__file__).resolve().parent.parent / "bundle-ctl/client/agent-takeover.py"
VALUE_FORMS = {
    "effort": re.compile("|".join(EFFORTS)),
    "model": re.compile("|".join(MODELS)),
    "parent": BUNDLE_NAME,
    "priority": re.compile(r"-?[0-9]{1,9}"),
    "queued": re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,20})?"),
    "session": BUNDLE_NAME,
    "terminal": re.compile(r"[0-9]{1,10}"),
    "where": re.compile(r"auto|local|worker(?::[a-z][a-z0-9-]{0,39})?"),
}

title_by_session = {}


def read_text(path):
    """The text of a regular file, None when it cannot be read. The bundle writes its `.agent/`
    folder: a link there is not followed, and a file of VALUE_FORMS gives None unless its value
    has its form, as the host puts them in paths, commands and the queue."""
    form = VALUE_FORMS.get(path.name)
    try:
        fd = open_unlinked(path, os.O_RDONLY)
    except OSError:
        return None
    with open(fd, errors="replace") as file:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        text = file.read(MAX_VALUE if form else -1).strip()
    return text if form is None or form.fullmatch(text) else None


def write_text(path, text):
    """Write a file of an `.agent/` folder, in place of a link the bundle left there."""
    with open(open_unlinked(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC), "w") as file:
        file.write(text)


def open_unlinked(path, flags):
    """The descriptor of `path` opened with `flags`, refused when its folder is a link. A link in
    place of the file is refused too, or replaced when `flags` create it."""
    flags |= os.O_NOFOLLOW | os.O_NONBLOCK
    folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            return os.open(path.name, flags, 0o644, dir_fd=folder)
        except OSError as error:
            if not flags & os.O_CREAT or error.errno != errno.ELOOP:
                raise
        os.unlink(path.name, dir_fd=folder)
        return os.open(path.name, flags, 0o644, dir_fd=folder)
    finally:
        os.close(folder)


def get_agent_folder(bundle):
    """The `.agent/` folder of a bundle, or the folder of an ops agent window (ops-SESSION)."""
    if bundle.startswith(OPS):
        return STATE / "ops" / bundle.removeprefix(OPS)
    return pathlib.Path(get_worktree_bundle_folder(bundle)) / ".agent"


def get_transcript(session):
    """The transcript of a Claude session of any container, None when it has none."""
    return next(PROJECTS.glob(f"*/{session}.jsonl"), None)


def get_session_records(container):
    """The newest record of each session in the sessions folder of a container, by session."""
    records = {}
    for path in pathlib.Path(CLAUDE_CONFIG_CONTAINER, container, "sessions").glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or not record.get("sessionId"):
            continue
        found = records.get(record["sessionId"], {})
        if (record.get("startedAt") or 0) >= (found.get("startedAt") or 0):
            records[record["sessionId"]] = record
    return records


def get_session_status(bundle, session, since=0):
    """What the claude of a bundle's session does, as its newest record says: `busy` in a turn,
    `waiting` on a dialog, `idle` or `shell` once the turn ended, None with no record or one
    started before `since` (ms): a claude stopped with its container leaves its record."""
    record = get_session_records(bundle).get(session, {})
    return record.get("status") if (record.get("startedAt") or 0) >= since else None


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


def is_handled(bundle, news, is_open=False):
    """Whether Seb opened the bundle in VS Code since its last agent task, nothing came since, and
    its window is open or was seen open less than HANDLED_FOR ago.

    The first call after his click keeps `news`, what waits on him then: a later one with an item
    it lacks is something new.
    """
    folder = get_agent_folder(bundle)
    opened = get_mtime(folder / "handled") or 0
    if opened <= (get_mtime(folder / "task.md") or 0):
        return False
    seen_open = folder / "handled.open"
    if is_open:
        seen_open.touch()
    if time.time() - max(opened, get_mtime(seen_open) or 0) > HANDLED_FOR:
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
