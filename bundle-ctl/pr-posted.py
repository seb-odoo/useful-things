#!/usr/bin/env python3
"""Tell the agent of a bundle what Seb posted on a PR from Firefox, as a PR event.

    pr-posted.py BUNDLE < LINE

The helper of firefox-gateway runs it (`tell` of its config.ini) once GitHub took a review or a
comment Seb sent from a tab of the Firefox group of the bundle, or of a PR whose branch is the
bundle. LINE names what he posted and where. It goes in .agent/pr-events/ with the lines of
client/prwatch.py, and reaches the agent the same ways.
"""

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from agents import get_agent_folder  # noqa: E402
from commands import BUNDLE_NAME  # noqa: E402

ADVICE = (
    "Seb posted it himself: read it with gh, and go on from it without asking him to confirm it (a"
    " review or an answer you prepared is sent; after his r+, mergebot has the PR). Open your reply"
    " on what he posted and what you did with it."
)
MAX_LINE = 2000


def load_prwatch():
    spec = importlib.util.spec_from_file_location("prwatch", HERE / "client" / "prwatch.py")
    prwatch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prwatch)
    return prwatch


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    bundle, line = sys.argv[1], sys.stdin.read().strip()
    if not line or "\n" in line or len(line) > MAX_LINE:
        sys.exit(f"one line of {MAX_LINE} characters at most on stdin")
    agent = get_agent_folder(bundle) if BUNDLE_NAME.fullmatch(bundle) else None
    if not agent or not agent.is_dir():
        sys.exit(f"{bundle} has no agent folder")
    load_prwatch().leave(line, agent / "pr-events", ADVICE)


if __name__ == "__main__":
    main()
