#!/usr/bin/env python3
"""Write .agent/done once a turn of an agent run ends on a verdict.

The run is either in a terminal (agent-terminal.py) or handed over to the Claude tab: agent-run.sh
stops `claude -p` at its first tool call and the tab reruns that turn. A turn that ends without a
verdict line (a background job still running) leaves .agent/waiting instead.
"""

import json
import pathlib
import re
import sys

AGENT = pathlib.Path("/workspace/.agent")
VERDICT = re.compile(r"(ready to push|needs seb:|blocked:|nothing to do:)", re.IGNORECASE)


def read(name):
    try:
        return (AGENT / name).read_text().strip()
    except OSError:
        return None


def main():
    hook = json.load(sys.stdin)
    if (
        hook.get("session_id") != read("session")
        or (read("handoff") is None and read("terminal") is None)
        or read("done") is not None
    ):
        return
    message = hook.get("last_assistant_message") or ""
    (AGENT / "result.md").write_text(message)
    first = next((line for line in message.splitlines() if line.strip()), "")
    if not VERDICT.match(first.strip().lstrip("#*_` ")):
        (AGENT / "waiting").touch()
        return
    (AGENT / "done.tmp").write_text("0\n")
    (AGENT / "done.tmp").replace(AGENT / "done")
    (AGENT / "waiting").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
