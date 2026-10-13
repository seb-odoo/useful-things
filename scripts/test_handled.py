#!/usr/bin/env python3
"""When a bundle counts as handled by Seb, and the group it goes to: python3 test_handled.py"""

import os
import sys
import tempfile

import agents
import branch_status
import commands


def main():
    found = {}
    with tempfile.TemporaryDirectory() as root:
        commands.WORKTREE_CONTAINER = root
        bundle = "master-handled--me"
        folder = agents.get_agent_folder(bundle)
        os.makedirs(folder.parent)
        found["never opened"] = agents.is_handled(bundle)
        agents.mark_handled(bundle)
        found["opened, no agent yet"] = agents.is_handled(bundle)
        (folder / "task.md").write_text("task")
        os.utime(folder / "handled", (1000, 1000))
        os.utime(folder / "task.md", (2000, 2000))
        found["agent task written after the open"] = agents.is_handled(bundle)
        agents.mark_handled(bundle)
        found["opened again after the agent task"] = agents.is_handled(bundle)
    pr = {"mergebot": None, "pending": False, "state": "open"}
    found["no PR goes to"] = branch_status.get_waiting_group(None)
    found["CI running goes to"] = branch_status.get_waiting_group({**pr, "pending": True})
    found["a draft goes to"] = branch_status.get_waiting_group({**pr, "state": "draft"})
    found["a ready PR goes to"] = branch_status.get_waiting_group(pr)
    expected = {
        "never opened": False,
        "opened, no agent yet": True,
        "agent task written after the open": False,
        "opened again after the agent task": True,
        "no PR goes to": "drafts",
        "CI running goes to": "ci",
        "a draft goes to": "drafts",
        "a ready PR goes to": "reviewer",
    }
    failures = [
        f"{case}: {found[case]}, expected {value}"
        for case, value in expected.items()
        if found[case] != value
    ]
    for line in failures:
        print(line)
    print(f"{len(expected) - len(failures)}/{len(expected)} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
