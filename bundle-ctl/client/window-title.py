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

An ops agent window (ops-SESSION) shows the title of its conversation in place of a name, and its
idle session is finished when its last answer has a Done part and neither a Decide nor a Your
action part, the parts of Seb's reports.
"""

import json
import os
import pathlib
import re
import sys

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
NEW = "new session"
OPS = "ops-"
PARTS = re.compile(r"^[#*_ ]*(Done|Decide|Your action)\b", re.MULTILINE)
PROJECTS = (
    pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
    / "projects"
)
TAIL = 256 * 1024
TITLES = {"ai-title": "aiTitle", "custom-title": "customTitle"}
WORKING = {"busy", "shell"}


def read_tail(session):
    path = next(PROJECTS.glob(f"*/{session}.jsonl"), None)
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


def is_finished(session):
    """Whether the last answer starts on the verdict of an agent whose task needs nothing more."""
    first = next((line for line in get_last_answer(session).splitlines() if line.strip()), "")
    return bool(FINISHED.match(first.strip().lstrip("#*_` ")))


def is_reported(session):
    """Whether the last answer reports work done and leaves nothing to Seb."""
    return set(PARTS.findall(get_last_answer(session))) == {"Done"}


def get_mark(status_by_session, finished=is_finished):
    statuses = set(status_by_session.values())
    if not statuses:
        return ""
    if "waiting" in statuses:
        return "✋"
    if statuses & WORKING:
        return "⏳"
    return "✅" if all(map(finished, status_by_session)) else "✋"


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
