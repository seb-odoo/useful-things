#!/usr/bin/env python3
"""Write .agent/done at the end of an agent run handed over to the Claude tab (Stop hook).

agent-run.sh stops `claude -p` at its first tool call and the tab reruns that turn, so the end of
the tab's turn is the end of the run.
"""

import json
import pathlib
import sys

AGENT = pathlib.Path("/workspace/.agent")


def read(name):
    try:
        return (AGENT / name).read_text().strip()
    except OSError:
        return None


def main():
    hook = json.load(sys.stdin)
    if (
        hook.get("session_id") != read("session")
        or read("handoff") is None
        or read("done") is not None
    ):
        return
    (AGENT / "result.md").write_text(hook.get("last_assistant_message") or "")
    (AGENT / "done.tmp").write_text("0\n")
    (AGENT / "done.tmp").replace(AGENT / "done")


if __name__ == "__main__":
    main()
