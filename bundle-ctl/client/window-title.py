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
window (ops-SESSION) shows the title of its conversation in place of a name, and guesses it from
the parts of the answer: finished with a Done part and neither a Decide nor a Your action part.
An idle session that waits for the reset of the usage limit works, as Claude goes on with it by
itself ("Usage limit reached · continuing automatically at 4pm"), until Esc cancels that, for 5
hours at most.
"""

import json
import os
import pathlib
import re
import sys
import time

CLOSE = re.compile(r"[*_\s]*(nothing (is )?left|(waiting|waits) (for|on) you)\b", re.IGNORECASE)
FINISHED = re.compile(r"(pushed:|nothing to do:)", re.IGNORECASE)
FLAGS = {"--failed": "❌", "--starting": "🚀"}
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
NEW = "new session"
OPS = "ops-"
PARTS = re.compile(r"^[#*_ ]*(Done|Decide|Your action)\b", re.MULTILINE)
PROJECTS = (
    pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
    / "projects"
)
RESUMING = "continuing automatically at"
RESUMING_FOR = 5 * 3600
SEPARATELY = re.compile(r"[*_\s]*Separately\s*:")
TAIL = 256 * 1024
TITLES = {"ai-title": "aiTitle", "custom-title": "customTitle"}


def get_transcript(session):
    return next(PROJECTS.glob(f"*/{session}.jsonl"), None)


def read_tail(session):
    path = get_transcript(session)
    try:
        with path.open("rb") as file:
            file.seek(max(0, file.seek(0, os.SEEK_END) - TAIL))
            return file.read().splitlines()
    except (AttributeError, OSError):
        return []


def get_topic(session):
    """The title Claude gave the conversation, or the one it was given by name."""
    titles = {}
    for line in read_tail(session):
        try:
            entry = json.loads(line)
            key = TITLES[entry["type"]]
            titles[key] = " ".join(str(entry[key]).split())
        except (KeyError, TypeError, ValueError):
            continue
    return titles.get("customTitle") or titles.get("aiTitle") or NEW


def get_last_answer(session):
    for line in reversed(read_tail(session)):
        try:
            entry = json.loads(line)
            content = entry["message"]["content"] if entry["type"] == "assistant" else []
            texts = [block["text"] for block in content if block["type"] == "text"]
        except (KeyError, TypeError, ValueError):
            continue
        if texts:
            return texts[-1]
    return ""


def is_resuming(session):
    """Whether a session waits for the reset of the usage limit, that Claude then goes on with by
    itself: its last notice says so, and no message nor other notice came after it. Not past
    RESUMING_FOR, the longest wait of a session limit: a claude that ended while it waited leaves
    that notice last, and one started later on the session does not go on by itself."""
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - RESUMING_FOR))
    for line in reversed(read_tail(session)):
        try:
            entry = json.loads(line)
            if entry["type"] in ("user", "assistant") or entry.get("subtype") == "informational":
                return RESUMING in str(entry.get("content")) and entry["timestamp"] > since
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return False


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


def get_mark(status_by_session, finished=is_finished):
    statuses = set(status_by_session.values())
    if not statuses:
        return ""
    if "waiting" in statuses:
        return "✋"
    if "busy" in statuses or any(map(is_resuming, status_by_session)):
        return "⏳"
    answers = map(get_last_answer, status_by_session)
    return "✅" if all(is_closed(answer, finished) for answer in answers) else "✋"


def get_title(bundle, status_by_session=None, flag=None):
    suffix = os.environ.get("BUNDLE_SUFFIX") or f"--{os.environ.get('USER', 'odoo')}"
    own = bundle.endswith(suffix)
    name = bundle.removesuffix(suffix)
    icon, kind = next((icon, kind) for icon, kind, matches in KINDS if matches(name, own))
    finished = is_reported if kind == "ops" else is_finished
    mark = FLAGS[flag] if flag else get_mark(status_by_session or {}, finished)
    label = get_topic(bundle.removeprefix(OPS)) if kind == "ops" else bundle
    return f"{mark} {icon} {label} ({kind})".lstrip()


if __name__ == "__main__":
    bundle, *args = sys.argv[1:]
    if args and args[0] in FLAGS:
        print(get_title(bundle, flag=args[0]))
    else:
        print(get_title(bundle, dict(pair.split("=", 1) for pair in args)))
