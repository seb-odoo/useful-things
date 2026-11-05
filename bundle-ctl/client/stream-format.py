#!/usr/bin/env python3
"""Print a `claude -p --output-format stream-json` run as a readable log, and its result to a file.

    claude -p ... --verbose --output-format stream-json | stream-format.py .agent/result.md
    ... | stream-format.py --until-tool-use .agent/result.md   # exit 0 at the first tool call, else 1
"""

import json
import pathlib
import sys

INPUT_KEYS = ("command", "file_path", "pattern", "path", "url", "description", "prompt")


def show(text, end="\n"):
    """Print text of the run with its control characters as `?`: the run and its transcript are
    the bundle's, and an escape there would reach the terminal of the host (security check 11-03,
    B19)."""
    print("".join(char if char.isprintable() or char in "\n\t" else "?" for char in text), end=end)


def summary(tool_input):
    for key in INPUT_KEYS:
        if value := tool_input.get(key):
            return str(value).splitlines()[0][:160]
    return ""


def main():
    *flags, result_path = sys.argv[1:]
    until_tool_use = "--until-tool-use" in flags
    for line in sys.stdin:
        try:
            event = json.loads(line)
        except ValueError:
            show(line, end="")
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            show(f"session {event.get('session_id')}, model {event.get('model')}")
            if mode := event.get("permissionMode"):
                pathlib.Path(result_path).with_name("mode").write_text(mode)
        elif event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    show(str(block["text"]))
                elif block.get("type") == "tool_use":
                    show(f"> {block['name']} {summary(block.get('input') or {})}")
                    if until_tool_use:
                        return 0
        elif event.get("type") == "result":
            result = event.get("result") or ""
            with open(result_path, "w") as file:
                file.write(result)
            show(
                f"\n{event.get('subtype')}: {event.get('num_turns')} turns, "
                f"{(event.get('duration_ms') or 0) // 60000} min, "
                f"${event.get('total_cost_usd') or 0:.2f}",
            )
        sys.stdout.flush()
    return 1 if until_tool_use else 0


if __name__ == "__main__":
    sys.exit(main())
