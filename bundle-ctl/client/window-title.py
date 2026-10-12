#!/usr/bin/env python3
"""Print the window title of a bundle: what its Claude sessions do, an icon of what its work is
about, its name, then that kind in words, last so that a narrow task bar button still shows the
name.

    window-title.py BUNDLE [--failed | --starting | SESSION=STATUS...]

STATUS is the `status` Claude writes in `~/.claude/sessions/<pid>.json` for a live session. No
session, no mark. --starting marks a window whose claude did not start yet, --failed one whose run
ended before its claude started.

agent-terminator.py names its window with it on the host, and claude-autoopen its VS Code window in
the container. The kind comes from the name alone, so a window opened by hand gets it too. The own
suffix is BUNDLE_SUFFIX, which the containers have, else `--$USER`, the default of config.py.
"""

import json
import os
import pathlib
import re
import sys

FINISHED = re.compile(r"(pushed:|nothing to do:)", re.IGNORECASE)
FLAGS = {"--failed": "❌", "--starting": "🚀"}
KINDS = (
    ("⏩", "fw", lambda name, own: name.endswith("-fw")),
    ("📝", "review", lambda name, own: not own),
    ("🤖", "runbot", lambda name, own: re.search(r"-runbot-\d+$|-flake$", name)),
    ("🎯", "task", lambda name, own: re.search(r"-\d{7,}$", name)),
    ("🧠", "analysis", lambda name, own: name.endswith("-code-analysis")),
    ("⭐", "mine", lambda name, own: True),
)
TAIL = 256 * 1024
TRANSCRIPTS = (
    pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
    / "projects"
    / "-workspace"
)
WORKING = {"busy", "shell"}


def get_last_answer(session):
    try:
        with (TRANSCRIPTS / f"{session}.jsonl").open("rb") as file:
            file.seek(max(0, file.seek(0, os.SEEK_END) - TAIL))
            lines = file.read().splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
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


def get_mark(status_by_session):
    statuses = set(status_by_session.values())
    if not statuses:
        return ""
    if "waiting" in statuses:
        return "✋"
    if statuses & WORKING:
        return "⏳"
    return "✅" if all(map(is_finished, status_by_session)) else "✋"


def get_title(bundle, status_by_session=None, flag=None):
    suffix = os.environ.get("BUNDLE_SUFFIX") or f"--{os.environ.get('USER', 'odoo')}"
    own = bundle.endswith(suffix)
    name = bundle.removesuffix(suffix)
    icon, kind = next((icon, kind) for icon, kind, matches in KINDS if matches(name, own))
    mark = FLAGS[flag] if flag else get_mark(status_by_session or {})
    return f"{mark} {icon} {bundle} ({kind})".lstrip()


if __name__ == "__main__":
    bundle, *args = sys.argv[1:]
    if args and args[0] in FLAGS:
        print(get_title(bundle, flag=args[0]))
    else:
        print(get_title(bundle, dict(pair.split("=", 1) for pair in args)))
