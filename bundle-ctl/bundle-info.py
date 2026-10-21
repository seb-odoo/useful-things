#!/usr/bin/env python3
"""Print what the bundle tab of Firefox shows of the agent of a bundle, as JSON: its session, the
close line of its last answer, and the end of its conversation.

    bundle-info.py BUNDLE

The helper of firefox-gateway runs it for the first tab of the Firefox group of the bundle. Each
item of `conversation` is a prompt (`user`), an answer (`assistant`) or a tool call (`tool`: its
name and what it acts on, as stream-format.py prints it), with its time; thinking, tool results,
notes of Claude Code and subagents aside. It keeps the last MAX_ITEMS items of the last TAIL bytes
of the transcript, each cut at MAX_TEXT but the last answer.
"""

import importlib.util
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from agents import SESSION_ID, get_agent_folder, get_transcript, read_text  # noqa: E402

INPUT_KEYS = ("command", "file_path", "pattern", "path", "url", "description", "prompt")
MAX_ITEMS = 120
MAX_TEXT = 6000
TAIL = 4 << 20


def load_title():
    spec = importlib.util.spec_from_file_location("title", HERE / "client" / "window-title.py")
    title = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(title)
    return title


def read_entries(path):
    with path.open("rb") as file:
        size = file.seek(0, os.SEEK_END)
        file.seek(max(0, size - TAIL))
        lines = file.read().splitlines()[size > TAIL:]
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            yield entry


def get_items(entry):
    if entry.get("type") not in ("assistant", "user") or entry.get("isSidechain") or entry.get("isMeta"):
        return []
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    items = []
    for block in content if isinstance(content, list) else []:
        if block.get("type") == "text" and block.get("text", "").strip():
            items.append({"role": entry["type"], "text": block["text"].strip()})
        elif block.get("type") == "tool_use":
            given = block.get("input") or {}
            target = next((str(given[key]).splitlines()[0] for key in INPUT_KEYS if given.get(key)), "")
            items.append({"role": "tool", "text": f"{block.get('name')}: {target[:160]}"})
    return [{**item, "time": entry.get("timestamp")} for item in items]


def get_info(bundle):
    session = read_text(get_agent_folder(bundle) / "session")
    session = session if SESSION_ID.fullmatch(session or "") else None
    transcript = session and get_transcript(session)
    items = [item for entry in read_entries(transcript) for item in get_items(entry)] if transcript else []
    answers = [item for item in items if item["role"] == "assistant"]
    for item in items:
        if len(item["text"]) > MAX_TEXT and (not answers or item is not answers[-1]):
            item["text"] = f"{item['text'][:MAX_TEXT]}..."
    close = load_title().get_close_line(answers[-1]["text"]) if answers else ""
    return {"bundle": bundle, "close": close, "conversation": items[-MAX_ITEMS:], "session": session}


def main():
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    print(json.dumps(get_info(sys.argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
