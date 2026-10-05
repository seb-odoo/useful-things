#!/usr/bin/env python3
"""Print a `claude -p --output-format stream-json` run as a readable log, and its result to a file.

    claude -p ... --verbose --output-format stream-json | stream-format.py .agent/result.md
"""

import json
import sys

INPUT_KEYS = ("command", "file_path", "pattern", "path", "url", "description", "prompt")


def summary(tool_input):
    for key in INPUT_KEYS:
        if value := tool_input.get(key):
            return str(value).splitlines()[0][:160]
    return ""


def main():
    result_path = sys.argv[1]
    for line in sys.stdin:
        try:
            event = json.loads(line)
        except ValueError:
            print(line, end="")
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            print(f"session {event.get('session_id')}, model {event.get('model')}")
        elif event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    print(block["text"])
                elif block.get("type") == "tool_use":
                    print(f"> {block['name']} {summary(block.get('input') or {})}")
        elif event.get("type") == "result":
            result = event.get("result") or ""
            with open(result_path, "w") as file:
                file.write(result)
            print(
                f"\n{event.get('subtype')}: {event.get('num_turns')} turns, "
                f"{(event.get('duration_ms') or 0) // 60000} min, "
                f"${event.get('total_cost_usd') or 0:.2f}",
            )
        sys.stdout.flush()


if __name__ == "__main__":
    main()
