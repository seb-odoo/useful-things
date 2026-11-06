#!/usr/bin/env python3
"""Hand the session of an agent running in a terminal over to the Claude tab of this window.

The claude-autoopen extension starts it when a window opens on a bundle whose agent runs in a
terminal (agent-terminal.py). It stops that `claude` where the tab can go on, then writes
.agent/handoff, on which the extension opens the tab on the session:

- a tool call is in flight: at once. The tab reruns that turn after one window reload, as for a run
  agent-run.sh stopped at its first tool call;
- the session is idle, as it is after its verdict, or its turn ended on a background command (a
  review watching its PR): at once, with .agent/restarted, so that the window is not reloaded.
  That command ends with the terminal's claude;
- else at the first of the two.

agent-terminal.py starts it the same way when another terminal opens on the bundle: that terminal
then goes on with the session.
"""

import json
import os
import pathlib
import signal
import stat
import time

AGENT = pathlib.Path("/workspace/.agent")
CONFIG = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
MAX_TRANSCRIPT = 64 << 20
QUIET = 120
START_WAIT = 90
STOP_WAIT = 10
TAIL = 1024 * 1024
TURN_WAIT = 600


def read(name):
    try:
        return (AGENT / name).read_text().strip()
    except OSError:
        return None


def find_claude(session):
    for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = path.read_bytes().split(b"\0")
        except OSError:
            continue
        if (
            os.path.basename(args[0]) == b"claude"
            and (b"--session-id" in args or b"--resume" in args)
            and session.encode() in args
        ):
            return int(path.parent.name)
    return None


def is_alive(pid):
    return pathlib.Path(f"/proc/{pid}").exists()


def get_status(session, pid):
    for path in (CONFIG / "sessions").glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if record.get("sessionId") == session and record.get("pid") == pid:
            return record.get("status")
    return None


def read_tail(transcript, tail):
    """The last `tail` bytes of a transcript, its first line dropped when cut. The host reads a
    bundle's transcript with it too, which the bundle may swap for a link or a FIFO after the
    host looked at it: no link followed, no wait on a FIFO, a regular file only (security check
    11-05, B101)."""
    try:
        fd = os.open(transcript, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return b""
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return b""
        try:
            file.seek(max(0, info.st_size - tail))
            data = file.read(tail)
        except OSError:
            return b""
    return data.partition(b"\n")[2] if info.st_size > tail else data


def read_entries(transcript, tail=TAIL):
    entries = []
    for line in read_tail(transcript, tail).split(b"\n"):
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def get_last_message(entries):
    """The author and the block types of the last message of the transcript, thinking left aside."""
    for entry in reversed(entries):
        if entry.get("type") not in ("assistant", "user") or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        blocks = (
            [block for block in content if isinstance(block, dict)]
            if isinstance(content, list)
            else []
        )
        kinds = {str(block.get("type")) for block in blocks}
        if kinds == {"thinking"}:
            continue
        return entry["type"], kinds
    return None, set()


def is_at_rest(transcript, author):
    """Whether the last turn ended a while ago, for a session that has no record.

    Two containers whose claude has the same pid write the same sessions/<pid>.json.
    """
    try:
        return author == "assistant" and time.time() - transcript.stat().st_mtime > QUIET
    except OSError:
        return False


def get_mode(transcript):
    """The last permission mode of a transcript, in its last TAIL bytes, else its last
    MAX_TRANSCRIPT ones: only the lines that name a mode are parsed, newest first."""
    for tail in (TAIL, MAX_TRANSCRIPT):
        data = read_tail(transcript, tail)
        end = len(data)
        while (found := data.rfind(b'"permissionMode"', 0, end)) >= 0:
            end = data.rfind(b"\n", 0, found) + 1
            stop = data.find(b"\n", found)
            try:
                entry = json.loads(data[end : stop if stop >= 0 else len(data)])
            except (ValueError, RecursionError):
                continue
            if isinstance(entry, dict) and isinstance(mode := entry.get("permissionMode"), str):
                return mode
    return None


def stop(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + STOP_WAIT
    while is_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    if is_alive(pid):
        os.kill(pid, signal.SIGKILL)
        while is_alive(pid):
            time.sleep(0.2)


def main():
    session = read("session")
    if not session:
        return
    pid = None
    deadline = time.monotonic() + START_WAIT
    # agent-terminal.py writes .agent/terminal before it starts the container, then claude.
    while (
        not (pid := find_claude(session))
        and (AGENT / "terminal").exists()
        and not (AGENT / "done").exists()
        and time.monotonic() < deadline
    ):
        time.sleep(1)
    rerun = False
    if pid:
        transcript = CONFIG / "projects" / "-workspace" / f"{session}.jsonl"
        deadline = time.monotonic() + TURN_WAIT
        while True:
            author, kinds = get_last_message(read_entries(transcript))
            rerun = author == "assistant" and "tool_use" in kinds
            status = get_status(session, pid)
            at_rest = status is None and not rerun and is_at_rest(transcript, author)
            if rerun or not is_alive(pid) or status in ("idle", "shell") or at_rest:
                break
            if time.monotonic() > deadline:
                rerun = True
                break
            time.sleep(0.5)
        if mode := get_mode(transcript):
            (AGENT / "mode").write_text(mode)
    if not rerun:
        (AGENT / "restarted").touch()
    # The terminal reads `handoff` as soon as its claude ends: write it first.
    (AGENT / "handoff").touch()
    if pid:
        stop(pid)
    (AGENT / "terminal").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
