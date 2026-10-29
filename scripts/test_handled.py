#!/usr/bin/env python3
"""When a bundle counts as handled by Seb, and the group it goes to: python3 test_handled.py"""

import os
import pathlib
import sys
import tempfile
import time

import agents
import branch_status
import commands

NEWS = ["odoo thread 2026-10-13T10:00:00Z"]
OTHER = [*NEWS, "odoo thread 2026-10-13T12:00:00Z"]


def main():
    found = {}
    with tempfile.TemporaryDirectory() as root:
        commands.WORKTREE_CONTAINER = root
        bundle = "master-handled--me"
        folder = agents.get_agent_folder(bundle)
        os.makedirs(folder.parent)
        found["never opened"] = agents.is_handled(bundle, NEWS)
        agents.mark_handled(bundle)
        found["opened, no agent yet"] = agents.is_handled(bundle, NEWS)
        found["nothing new since"] = agents.is_handled(bundle, NEWS)
        found["something new since"] = agents.is_handled(bundle, OTHER)
        found["less than before"] = agents.is_handled(bundle, [])
        (folder / "task.md").write_text("task")
        os.utime(folder / "handled", (1000, 1000))
        os.utime(folder / "handled.json", (1000, 1000))
        os.utime(folder / "task.md", (2000, 2000))
        found["agent task written after the open"] = agents.is_handled(bundle, NEWS)
        agents.mark_handled(bundle)
        found["opened again after the agent task"] = agents.is_handled(bundle, OTHER)
        two_days_ago = time.time() - 2 * 86400
        os.utime(folder / "handled", (two_days_ago, two_days_ago))
        found["opened two days ago, never seen open"] = agents.is_handled(bundle, OTHER)
        found["opened two days ago, window open"] = agents.is_handled(bundle, OTHER, is_open=True)
        found["closed since"] = agents.is_handled(bundle, OTHER)
        os.utime(folder / "handled.open", (two_days_ago, two_days_ago))
        found["closed two days ago"] = agents.is_handled(bundle, OTHER)
    pr = {"mergebot": None, "pending": False, "state": "open"}
    found["no PR goes to"] = branch_status.get_waiting_group(None)
    found["CI running goes to"] = branch_status.get_waiting_group({**pr, "pending": True})
    found["a draft goes to"] = branch_status.get_waiting_group({**pr, "state": "draft"})
    found["a ready PR goes to"] = branch_status.get_waiting_group(pr)
    facts = {**pr, "answers": ["d"], "delegated": True, "failing": [{"context": "ci/runbot"}]}
    facts.update(head="a", mergebot="error", review="CHANGES_REQUESTED")
    rows = {"enterprise": {"conflict": None, "pr": None}, "odoo": {"conflict": True, "pr": facts}}
    found["what waits on me"] = branch_status.get_news(rows)
    with tempfile.TemporaryDirectory() as root:
        branch_status.MAIL_AGENT = pathlib.Path(root)
        (branch_status.MAIL_AGENT / "logins.json").write_text('{"logins": {"teammate": "tmt"}}')
        (branch_status.MAIL_AGENT / "team.json").write_text('{"tmt": "Teammate"}')
        branch_status.get_team.cache_clear()
        found["team"] = branch_status.get_team()

    def note(login, body, at="2026-10-15T06:13:02Z"):
        member = "MEMBER" if login in ("member", "fw-bot", "robodoo") else "NONE"
        return {
            "author": {"login": login},
            "authorAssociation": member,
            "body": body,
            "createdAt": at,
            "submittedAt": at,
        }

    def pull(*comments, reviews=()):
        red = {"context": "ci/runbot", "state": "FAILURE", "targetUrl": "https://runbot/1"}
        head = {"committedDate": "2026-10-14T15:00:00Z", "status": {"contexts": [red]}}
        asked = {"actor": {"login": "me"}, "createdAt": "2026-10-14T16:00:00Z"}
        ping = "@pinged this PR targets master and is the last of the forward-port chain. @later"
        return {
            "asked": None,
            "author": {"login": "me"},
            "auto_asked": 0,
            "comments": {"nodes": [note("fw-bot", ping, "2026-10-14T16:00:00Z"), *comments]},
            "commits": {"nodes": [{"commit": head}]},
            "headRefName": "master-x--me",
            "headRefOid": "a",
            "isDraft": False,
            "mergebot": None,
            "number": 1,
            "pushed": 0,
            "reviewDecision": None,
            "reviewThreads": {"nodes": []},
            "reviews": {"nodes": list(reviews)},
            "state": "OPEN",
            "timelineItems": {"nodes": [{**asked, "requestedReviewer": {"login": "asked"}}]},
            "url": "https://github.com/odoo/odoo/pull/1",
            "viewer": "me",
        }

    def facts(login, command):
        return branch_status.get_pr_facts(pull(note(login, command)), "odoo", time.time())

    overrides = pull(
        note("me", "@robodoo override=ci/security", "2026-10-14T09:00:00Z"),
        reviews=[
            note("teammate", "@robodoo override=ci/style,ci/runbot"),
            note("stranger", "@robodoo override=ci/legal"),
        ],
    )
    notes = branch_status.get_command_notes(overrides)
    found["checks overridden since the head"] = sorted(
        branch_status.get_overridden(overrides, notes)
    )
    override = "@robodoo override=ci/runbot"
    found["a red check after an override=ci/runbot of"] = {
        login: [check["context"] for check in facts(login, override)["failing"]]
        for login in ("stranger", "later", "robodoo", "me", "teammate", "asked", "pinged", "member")
    }
    found["delegated by"] = {
        login: facts(login, "@robodoo delegate+")["delegated"]
        for login in ("stranger", "asked", "member")
    }
    expected = {
        "never opened": False,
        "opened, no agent yet": True,
        "nothing new since": True,
        "something new since": False,
        "less than before": True,
        "agent task written after the open": False,
        "opened again after the agent task": True,
        "opened two days ago, never seen open": False,
        "opened two days ago, window open": True,
        "closed since": True,
        "closed two days ago": False,
        "no PR goes to": "drafts",
        "CI running goes to": "ci",
        "a draft goes to": "drafts",
        "a ready PR goes to": "reviewer",
        "team": {"teammate"},
        "checks overridden since the head": ["ci/runbot", "ci/style"],
        "a red check after an override=ci/runbot of": {
            "stranger": ["ci/runbot"],
            "later": ["ci/runbot"],
            "robodoo": ["ci/runbot"],
            "me": [],
            "teammate": [],
            "asked": [],
            "pinged": [],
            "member": [],
        },
        "delegated by": {"stranger": False, "asked": True, "member": True},
        "what waits on me": [
            "odoo changes requested",
            "odoo conflict a",
            "odoo delegated",
            "odoo failing ci/runbot a",
            "odoo staging error a",
            "odoo thread d",
        ],
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
