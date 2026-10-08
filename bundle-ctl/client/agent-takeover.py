#!/usr/bin/env python3
"""Hand the session of an agent running in a terminal over to the Claude tab of this window.

The claude-autoopen extension starts it when a window opens on a bundle whose agent runs in a
terminal (agent-terminal.py). It stops that `claude` where the tab can go on, then writes
.agent/handoff, on which the extension opens the tab on the session:

- a tool call is in flight: at once. The tab reruns that turn after one window reload, as for a run
  agent-run.sh stopped at its first tool call;
- the session is idle, as it is after its verdict: at once, with .agent/restarted, so that the
  window is not reloaded;
- else at the first of the two.
"""

import json
import os
import pathlib
import signal
import time

AGENT = pathlib.Path("/workspace/.agent")
CONFIG = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
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
            and b"--session-id" in args
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


def read_entries(transcript, tail=TAIL):
    try:
        with transcript.open("rb") as file:
            size = file.seek(0, os.SEEK_END)
            file.seek(max(0, size - tail) if tail else 0)
            lines = file.read().split(b"\n")
    except OSError:
        return []
    if tail and size > tail:
        lines = lines[1:]
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def is_in_tool_call(entries):
    """Whether the transcript ends on a tool call with no result yet, thinking left aside."""
    for entry in reversed(entries):
        if entry.get("type") not in ("assistant", "user") or entry.get("isSidechain"):
            continue
        content = entry.get("message", {}).get("content")
        kinds = {block.get("type") for block in content} if isinstance(content, list) else set()
        if kinds == {"thinking"}:
            continue
        return entry["type"] == "assistant" and "tool_use" in kinds
    return False


def get_mode(transcript):
    for tail in (TAIL, 0):
        for entry in reversed(read_entries(transcript, tail)):
            if mode := entry.get("permissionMode"):
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
            rerun = is_in_tool_call(read_entries(transcript))
            if rerun or not is_alive(pid) or get_status(session, pid) == "idle":
                break
            if time.monotonic() > deadline:
                rerun = True
                break
            time.sleep(0.5)
        if mode := get_mode(transcript):
            (AGENT / "mode").write_text(mode)
        stop(pid)
    if not rerun:
        (AGENT / "restarted").touch()
    (AGENT / "handoff").touch()
    (AGENT / "terminal").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
