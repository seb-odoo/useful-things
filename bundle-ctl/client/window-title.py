#!/usr/bin/env python3
"""Print the window title of a bundle: what its Claude sessions do, an icon of what its work is
about, its name, then that kind in words, last so that a narrow task bar button still shows the
name.

    window-title.py BUNDLE [--failed | --starting | SESSION=STATUS...]
    window-title.py ops-SESSION [--starting | SESSION=STATUS...]

STATUS is the `status` Claude writes in `~/.claude/sessions/<pid>.json` for a live session. No
session, no mark. --starting marks a window whose claude did not start yet, --failed one whose run
ended before its claude started.

agent-terminator.py names its window with it on the host, and claude-autoopen its VS Code window in
the container. The kind comes from the name alone, so a window opened by hand gets it too. The own
suffix is BUNDLE_SUFFIX, which the containers have, else `--$USER`, the default of config.py.

An idle session is finished when the close line of its last answer, the line that ends Seb's
reports, starts with "Nothing left", and waits for him when it starts with "Waiting for you". With
no close line, a bundle guesses it from the verdict on the first line of that answer. An ops agent
window (ops-SESSION) shows the title of its conversation in place of a name, else the first line of
its first prompt, and guesses it from the parts of the answer: finished with a Done part and
neither a Decide nor a Your action part.
An idle session that waits for the reset of the usage limit works, as Claude goes on with it by
itself ("Usage limit reached · continuing automatically at 4pm"), until Esc cancels that, for 5
hours at most. A busy session whose running call waits for Seb's answer in hok (`hok-answer`,
`hctl job`) waits for him, but not an `hctl job` of a request that hok told his yes to: that one
waits for the run.
"""

import calendar
import json
import os
import pathlib
import re
import stat
import sys
import time

CLOSE = re.compile(r"[*_\s]*(nothing (is )?left|(waiting|waits) (for|on) you)\b", re.IGNORECASE)
FINISHED = re.compile(r"(pushed:|nothing to do:)", re.IGNORECASE)
FLAGS = {"--failed": "❌", "--starting": "🚀"}
HOK_WAIT = re.compile(r"\b(hok-answer|hctl job)\b")
JOB = re.compile(r"\bhctl job (\d+)")
SAID_YES = re.compile(rb"Seb said yes to your requests? (\d+(?:, \d+)*)")
KINDS = (
    ("🔧", "ops", lambda name, own: name.startswith(OPS)),
    ("⏩", "fw", lambda name, own: name.endswith("-fw")),
    ("📝", "review", lambda name, own: not own),
    ("🤖", "runbot", lambda name, own: re.search(r"-runbot-\d+$|-flake$", name)),
    ("🎯", "task", lambda name, own: re.search(r"-\d{7,}$", name)),
    ("🧠", "analysis", lambda name, own: name.endswith("-code-analysis")),
    ("⭐", "mine", lambda name, own: True),
)
LEFT = re.compile(r"[*_\s]*nothing (is )?left\b", re.IGNORECASE)
LINK = re.compile(r"\[([^\]]+)\]\([^)\s]+\)")
LOGIN_WAIT = "claude-switch: the main login expires"
MAX_LINE = 4 << 20
MAX_SCAN = 16 << 20
NEW = "new session"
OPS = "ops-"
PARTS = re.compile(r"^[#*_ ]*(Done|Decide|Your action)\b", re.MULTILINE)
PROMPT = 50
HOST_PROJECT = re.sub(
    r"[^A-Za-z0-9]", "-", os.environ.get("REPO_ROOT") or str(pathlib.Path.home() / "repo")
)
PROJECTS = (
    pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
    / "projects"
)
# The transcripts of each bundle on the host, a folder per bundle (devcontainer/transcripts.py).
TRANSCRIPTS = (
    pathlib.Path(os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share")
    / "claude-transcripts"
)
RESUMING = "continuing automatically at"
SESSION_NAME = re.compile(r"[\w-]+")
BUNDLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
RESUMING_FOR = 5 * 3600
RETRIES = 5
SEPARATELY = re.compile(r"[*_\s]*Separately\s*:")
TAIL = 256 * 1024
TITLES = {"ai-title": "aiTitle", "custom-title": "customTitle"}


def get_projects(bundle):
    """The folders that hold the transcripts of a bundle, or of an ops window: never a folder of
    the other kind, as every bundle wrote `-workspace` (security check 11-03, B5). An ops
    session is in the ops container's own folder, or in the host's for one of before. A bundle's
    is in its own folder on the host (B7), else in `-workspace`, its own in its container."""
    if bundle.startswith(OPS):
        return [PROJECTS / f"ops{HOST_PROJECT}", PROJECTS / HOST_PROJECT]
    named = [TRANSCRIPTS / bundle] if BUNDLE_NAME.fullmatch(bundle or "") else []
    return [*named, PROJECTS / "-workspace"]


def get_transcript(session, bundle=""):
    if not SESSION_NAME.fullmatch(session or ""):
        return None
    paths = [folder / f"{session}.jsonl" for folder in get_projects(bundle)]
    return next((path for path in paths if path.is_file() and not path.is_symlink()), None)


def open_transcript(session, bundle=""):
    """The transcript of a session open for reading, None when it has none: a regular file, with
    no link followed and no wait on a FIFO, as a bundle writes its transcripts (security check
    11-03, B13)."""
    try:
        fd = os.open(get_transcript(session, bundle), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (OSError, TypeError):
        return None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    return os.fdopen(fd, "rb")


def read_tail(session, bundle=""):
    try:
        with open_transcript(session, bundle) as file:
            file.seek(max(0, file.seek(0, os.SEEK_END) - TAIL))
            return file.read(TAIL).splitlines()
    except (AttributeError, OSError, TypeError):
        return []


def get_first_prompt(session, bundle=""):
    """The first line of the first prompt typed in a session, PROMPT characters at most."""
    try:
        with open_transcript(session, bundle) as file:
            lines = file.read(TAIL).splitlines()
    except (AttributeError, OSError, TypeError):
        return None
    for line in lines:
        try:
            entry = json.loads(line)
            content = entry["message"]["content"] if entry["type"] == "user" else None
        except (KeyError, TypeError, ValueError):
            continue
        origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
        if (
            origin.get("kind", "human") != "human"
            or entry.get("isMeta")
            or entry.get("isSidechain")
        ):
            continue
        if isinstance(content, list):
            content = "\n".join(
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        text = str(content or "").strip()
        if text and not text.startswith("<"):
            first = " ".join(text.splitlines()[0].split())
            return first if len(first) <= PROMPT else f"{first[: PROMPT - 3].rstrip()}..."
    return None


def get_topic(session, bundle=""):
    """The title Claude gave the conversation, or the one it was given by name, else its first
    prompt: Claude gives no title to a prompt of a word or two ("triage", on 2026-11-02)."""
    titles = {}
    for line in read_tail(session, bundle):
        try:
            entry = json.loads(line)
            key = TITLES[entry["type"]]
            titles[key] = " ".join(str(entry[key]).split())
        except (KeyError, TypeError, ValueError):
            continue
    return titles.get("customTitle") or titles.get("aiTitle") or get_first_prompt(session, bundle) or NEW


def get_last_answer(session, bundle=""):
    for line in reversed(read_tail(session, bundle)):
        try:
            entry = json.loads(line)
            content = entry["message"]["content"] if entry["type"] == "assistant" else []
            texts = [block["text"] for block in content if block["type"] == "text"]
        except (KeyError, TypeError, ValueError):
            continue
        if texts:
            return texts[-1]
    return ""


def is_resuming(session, bundle=""):
    """Whether a session waits for the reset of the usage limit, that Claude then goes on with by
    itself: its last notice says so, and no message nor other notice came after it. Not past
    RESUMING_FOR, the longest wait of a session limit: a claude that ended while it waited leaves
    that notice last, and one started later on the session does not go on by itself."""
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - RESUMING_FOR))
    for line in reversed(read_tail(session, bundle)):
        try:
            entry = json.loads(line)
            if entry["type"] in ("user", "assistant") or entry.get("subtype") == "informational":
                return RESUMING in str(entry.get("content")) and entry["timestamp"] > since
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return False


def read_answers(session, bundle=""):
    """The answers of the main thread of a session, the last one first, read TAIL bytes at a time
    up its transcript: a turn that ends on an API error writes the blobs of its request first,
    77 KB a turn in a bundle on 2026-10-30, so that TAIL held 3 such turns, never the 6 of the
    requeue, and its terminal typed GO_ON 423 times. MAX_SCAN bytes at most, and a line longer
    than MAX_LINE is dropped."""
    try:
        with open_transcript(session, bundle) as file:
            end, rest = file.seek(0, os.SEEK_END), b""
            last = max(0, end - MAX_SCAN)
            while end > last:
                start = max(last, end - TAIL)
                file.seek(start)
                lines = (file.read(end - start) + rest).split(b"\n")
                rest = lines.pop(0) if start else b""
                rest = rest if len(rest) <= MAX_LINE else b""
                for line in reversed(lines):
                    try:
                        entry = json.loads(line)
                        if entry["type"] == "assistant" and not entry.get("isSidechain"):
                            yield entry
                    except (KeyError, TypeError, ValueError):
                        continue
                end = start
    except (AttributeError, OSError, TypeError):
        return


def count_api_errors(session, most=RETRIES + 1, bundle=""):
    """How many turns in a row of a session ended on an API error that a new request may pass: a
    5xx, the API out of reach, an answer cut; `most` at most. 0 when its last message is anything
    else. A usage limit is not one: Claude goes on with it at the reset."""
    count = 0
    for entry in read_answers(session, bundle):
        if count == most or not entry.get("isApiErrorMessage"):
            return count
        if entry.get("error") != "server_error":
            return count
        count += 1
    return count


def is_login_wait(session, bundle=""):
    """Whether the last turn of a session ended on the 529 claude-switch gives a dummy login while
    the main login expires: a new request passes once a laptop session or bundle-ctl renewed it,
    not before."""
    for entry in read_answers(session, bundle):
        return bool(entry.get("isApiErrorMessage")) and LOGIN_WAIT in str(entry.get("message"))
    return False


def is_hok_wait(call, told):
    command = (call.get("input") or {}).get("command") if call.get("name") == "Bash" else None
    job = JOB.search(str(command or ""))
    return bool(HOK_WAIT.search(str(command or ""))) and not (job and job[1] in told)


def get_hok_wait(session, bundle=""):
    """When a session began to wait for Seb's answer in hok, as epoch seconds: the start of the
    calls of hok-answer or `hctl job` it made in a row, the last one still running. None when no
    such call runs: answered, sent to the background, or another call runs. An `hctl job` of a
    request that hok told his yes to waits for its run, not for him."""
    lines = read_tail(session, bundle)
    told = {
        number
        for line in lines
        if b"said yes" in line
        for match in SAID_YES.finditer(line)
        for number in match[1].decode().split(", ")
    }
    answered, start, running = set(), None, False
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            content = entry["message"]["content"] if entry["type"] in ("user", "assistant") else None
        except (KeyError, TypeError, ValueError):
            continue
        blocks = [block for block in content if isinstance(block, dict)] if content else []
        if entry["type"] == "user":
            if not blocks or any(block.get("type") != "tool_result" for block in blocks):
                break
            answered.update(block.get("tool_use_id") for block in blocks)
            continue
        calls = [block for block in blocks if block.get("type") == "tool_use"]
        if not all(is_hok_wait(call, told) for call in calls):
            break
        if calls:
            start = calendar.timegm(time.strptime(entry["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
            running = running or any(call.get("id") not in answered for call in calls)
    return start if running else None


def get_close_line(answer):
    """The last line of an answer when it says whether Seb can close the session, as the Stop hook
    guard-report-end.py wants a report to end: only Separately: lines may follow it. Without its
    bold, and with the text of its links in place of the links, "" when it has none."""
    lines = [line for line in answer.splitlines() if line.strip()]
    while lines and SEPARATELY.match(lines[-1]):
        lines.pop()
    if not lines or not CLOSE.match(lines[-1]):
        return ""
    return LINK.sub(r"\1", lines[-1].replace("**", "")).strip()


def is_finished(answer):
    """Whether the answer starts on the verdict of an agent whose task needs nothing more."""
    first = next((line for line in answer.splitlines() if line.strip()), "")
    return bool(FINISHED.match(first.strip().lstrip("#*_` ")))


def is_reported(answer):
    """Whether the answer reports work done and leaves nothing to Seb."""
    return set(PARTS.findall(answer)) == {"Done"}


def is_closed(answer, finished):
    """Whether Seb can close the session: its close line says nothing is left, else finished
    guesses it from the rest of the answer."""
    close = get_close_line(answer)
    return bool(LEFT.match(close)) if close else finished(answer)


def get_mark(status_by_session, finished=is_finished, bundle=""):
    statuses = set(status_by_session.values())
    if not statuses:
        return ""
    busy = [session for session, status in status_by_session.items() if status == "busy"]
    if "waiting" in statuses or any(get_hok_wait(session, bundle) for session in busy):
        return "✋"
    if busy or any(is_resuming(session, bundle) for session in status_by_session):
        return "⏳"
    answers = [get_last_answer(session, bundle) for session in status_by_session]
    return "✅" if all(is_closed(answer, finished) for answer in answers) else "✋"


def get_title(bundle, status_by_session=None, flag=None, where=None):
    suffix = os.environ.get("BUNDLE_SUFFIX") or f"--{os.environ.get('USER', 'odoo')}"
    own = bundle.endswith(suffix)
    name = bundle.removesuffix(suffix)
    icon, kind = next((icon, kind) for icon, kind, matches in KINDS if matches(name, own))
    finished = is_reported if kind == "ops" else is_finished
    mark = FLAGS[flag] if flag else get_mark(status_by_session or {}, finished, bundle)
    label = get_topic(bundle.removeprefix(OPS), bundle) if kind == "ops" else bundle
    label = f"{label} @{where}" if where else label
    return f"{mark} {icon} {label} ({kind})".lstrip()


if __name__ == "__main__":
    bundle, *args = sys.argv[1:]
    where = args.pop(0).removeprefix("@") if args and args[0].startswith("@") else None
    if args and args[0] in FLAGS:
        print(get_title(bundle, flag=args[0], where=where))
    else:
        print(get_title(bundle, dict(pair.split("=", 1) for pair in args), where=where))
