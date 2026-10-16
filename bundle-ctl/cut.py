"""The Claude sessions a crash or a restart cut, in the bundle containers and in the ops container.

A session is cut when no claude runs it any more, its transcript was written in the last WINDOW,
and its last turn had not ended:

- its last message is a tool call with no result, or a tool result or a prompt with no answer: an
  interruption by Seb, a command of his such as /model and the summary of a compaction aside;
- or that turn ended while a background command or agent it started still ran, whose end would
  have started the next turn, on a short answer: not a report (a Done, Decide or Your action part,
  or more than 15 lines, as for the Stop hook guard-report-end.py), no verdict on its first line
  and no close line ("Nothing left", "Waiting for you") on its last.

A `claude -p` run (`sdk-cli`) is never cut: nobody waits for it.
"""

import json
import os
import re
import time

CLOSE = re.compile(r"[*_\s]*(nothing (is )?left|(waiting|waits) (for|on) you)\b", re.IGNORECASE)
GO_ON = "Continue from where you left off."
INTERRUPTED = "[Request interrupted by user"
LOCAL = ("<bash-", "<command-", "<local-command-")
NOTIFIED = re.compile(rb"<task-id>([^<]+)</task-id>")
PARTS = re.compile(r"^\s*(#+\s*|\*\*)(Done|Decide|Your action)\b", re.IGNORECASE | re.MULTILINE)
REPORT_LINES = 15
SEPARATELY = re.compile(r"[*_\s]*Separately\s*:")
SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SHORT = 160
STOPS = {"KillShell": "shell_id", "TaskStop": "task_id"}
TAIL = 256 * 1024
TITLES = {"ai-title": "aiTitle", "custom-title": "customTitle"}
VERDICT = re.compile(r"(pushed:|ready to push|needs seb:|blocked:|nothing to do:)", re.IGNORECASE)
WINDOW = 3 * 86400


def shorten(text, size=SHORT):
    text = " ".join(str(text).split())
    return text if len(text) <= size else f"{text[: size - 3]}..."


def read_lines(path, tail):
    with path.open("rb") as file:
        size = file.seek(0, os.SEEK_END)
        file.seek(max(0, size - tail))
        lines = file.read().splitlines()
    return lines[1:] if size > tail else lines


def get_text(content):
    if isinstance(content, str):
        return content
    blocks = content if isinstance(content, list) else []
    return "\n".join(
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )


def describe_call(block):
    """A tool call in one line: its tool and what it ran or touched."""
    given = block.get("input") if isinstance(block.get("input"), dict) else {}
    keys = ("command", "file_path", "description", "skill", "prompt", "url", "pattern")
    what = next((given[key] for key in keys if given.get(key)), "") or json.dumps(given)
    return shorten(f"{block.get('name')} {what}")


def is_prompt(entry, text, kinds):
    """Whether a message is a prompt typed in the session, the resume prompts aside."""
    origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
    return (
        origin.get("kind", "human") == "human"
        and not entry.get("isMeta")
        and "tool_result" not in kinds
        and bool(text.strip())
        and not text.lstrip().startswith(("<", INTERRUPTED, GO_ON))
    )


def parse(lines):
    titles, started, ended = {}, set(), set()
    last = prompt = call = None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        ended.update(NOTIFIED.findall(line))
        kind = entry.get("type")
        if kind in TITLES:
            titles[kind] = shorten(entry.get(TITLES[kind]) or "")
        if kind not in ("user", "assistant") or entry.get("isSidechain"):
            continue
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        blocks = [block for block in blocks if isinstance(block, dict)]
        kinds = {block.get("type") for block in blocks}
        text = get_text(content)
        if (
            kinds == {"thinking"}
            or entry.get("isCompactSummary")
            or (kind == "user" and text.lstrip().startswith(LOCAL))
        ):
            continue
        last = {"author": kind, "entrypoint": entry.get("entrypoint"), "kinds": kinds, "text": text}
        for block in blocks:
            if block.get("type") != "tool_use":
                continue
            call = describe_call(block)
            given = block.get("input") if isinstance(block.get("input"), dict) else {}
            stop = STOPS.get(block.get("name"))
            if stop and given.get(stop):
                ended.add(str(given[stop]).encode())
        result = entry.get("toolUseResult")
        if isinstance(result, dict):
            agent = result.get("isAsync") and result.get("agentId")
            if task := result.get("backgroundTaskId") or agent:
                started.add(str(task).encode())
        if kind == "user" and is_prompt(entry, text, kinds):
            prompt = text
    return {
        "background": len(started - ended),
        "call": call,
        "last": last,
        "prompt": prompt,
        "title": titles.get("custom-title") or titles.get("ai-title"),
    }


def read_session(path, tail=TAIL):
    """What the end of a transcript says: its title, its last message, the last prompt typed in it,
    its last tool call, and how many of the background commands and agents it started never ended.
    A tail that holds no message is read again four times longer, up to the whole file."""
    size = path.stat().st_size
    while True:
        session = parse(read_lines(path, tail))
        if session["last"] or tail >= size:
            return session
        tail *= 4


def is_short(answer):
    """Whether an answer only says where the work is: no report, no verdict and no close line."""
    lines = [line for line in answer.splitlines() if line.strip()]
    while lines and SEPARATELY.match(lines[-1]):
        lines.pop()
    return bool(lines) and not (
        len(lines) > REPORT_LINES
        or PARTS.search(answer)
        or VERDICT.match(lines[0].strip().lstrip("#*_` "))
        or CLOSE.match(lines[-1])
    )


def get_cut(session):
    """Why the last turn of a session no claude runs had not ended, None when it had."""
    last = session["last"]
    if not last or last["entrypoint"] == "sdk-cli":
        return None
    if last["author"] == "assistant" and "tool_use" in last["kinds"]:
        return "a tool call ran"
    if last["author"] == "user":
        if last["text"].lstrip().startswith(INTERRUPTED):
            return None
        if "tool_result" in last["kinds"]:
            return "a tool result had no answer"
        return "a prompt had no answer"
    if session["background"] and is_short(last["text"]):
        return "it waited for a background command or agent"
    return None


def get_live_sessions(records, proc="/proc"):
    """The sessions a claude runs now, on the host or in a container: named on its command line, or
    by the record of a live claude in `records`, the sessions folders of the containers. A record
    names the pid of its claude in the container and when it started, which the /proc of the host
    shows as the last NSpid and the starttime."""
    running, live = set(), set()
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            with open(f"{proc}/{name}/cmdline", "rb") as file:
                args = file.read().split(b"\0")
            if os.path.basename(args[0]) != b"claude":
                continue
            with open(f"{proc}/{name}/status") as file:
                nspid = re.search(r"^NSpid:(.*)$", file.read(), re.MULTILINE)[1].split()[-1]
            with open(f"{proc}/{name}/stat") as file:
                start = file.read().rpartition(")")[2].split()[19]
        except (IndexError, OSError, TypeError):
            continue
        running.add((nspid, start))
        words = (arg.decode(errors="replace") for arg in args)
        live.update(word for word in words if SESSION_ID.fullmatch(word))
    for folder in records:
        for path in folder.glob("*.json"):
            try:
                record = json.loads(path.read_text())
                if (str(record["pid"]), str(record["procStart"])) in running:
                    live.add(record["sessionId"])
            except (KeyError, OSError, TypeError, ValueError):
                continue
    return live


def get_prompt(row):
    """The prompt a cut session goes on with: what stopped it, and what to check before its last
    call runs again."""
    when = time.strftime("%H:%M on %Y-%m-%d", time.localtime(row["stopped"]))
    call = f"\n\nYour last call: {row['call']}" if row.get("call") else ""
    return (
        f"{GO_ON}\n\nThis session was cut at {when}: its claude stopped with its container or its"
        " window, after a crash or a restart, nothing you did. The background commands, servers"
        " and agents of the session died with it: start again the ones you still need. Before you"
        " run your last call again, check what it already did (a commit, a push, a file written,"
        f" a request held in hok), so that nothing is done twice.{call}"
    )
