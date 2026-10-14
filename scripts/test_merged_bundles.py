#!/usr/bin/env python3
"""Which merged bundles gbs deletes, on scratch repos: python3 test_merged_bundles.py"""

from contextlib import redirect_stdout
import io
import os
import subprocess
import sys
import tempfile

import branch_status
import commands
import delete_bundle
from rich.console import Console
from rich.live import Live
from utils import UtilsRunner

REPOS = ("enterprise", "odoo")
GIT = ["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]


def git(cwd, *args):
    return subprocess.run(
        [*GIT, *args], capture_output=True, check=True, cwd=cwd, text=True
    ).stdout.strip()


def main():
    runner = UtilsRunner(live=Live("", auto_refresh=False, console=Console(quiet=True)))
    found = {}
    with tempfile.TemporaryDirectory() as root:
        commands.folder_by_repo.clear()
        commands.folder_by_repo.update({repo: f"{root}/{repo}" for repo in REPOS})
        commands.WORKTREE_CONTAINER = f"{root}/src"
        delete_bundle.CLAUDE_CONFIG_CONTAINER = f"{root}/claude-config"
        delete_bundle.EXTENSIONS_CONTAINER = f"{root}/extensions"
        delete_bundle._bundle_databases = lambda runner, bundle_name: []
        for repo in REPOS:
            folder = commands.get_repo_folder(repo)
            git(root, "init", folder)
            git(root, "init", "--bare", f"{repo}-odoo.git")
            git(folder, "remote", "add", "odoo", f"{root}/{repo}-odoo.git")
            git(folder, "commit", "--allow-empty", "-m", "base")
            git(folder, "push", "odoo", "HEAD:refs/heads/master")
            git(folder, "fetch", "odoo")
            git(root, "init", "--bare", f"{repo}-odoo-dev.git")
            git(folder, "remote", "add", "odoo-dev", f"{root}/{repo}-odoo-dev.git")

        def make(bundle, repo, commits=1):
            os.makedirs(commands.get_worktree_bundle_folder(bundle), exist_ok=True)
            runner.add_worktree(
                repo=repo, bundle_name=bundle, make_branch=True, target_ref="odoo/master"
            )
            folder = commands.get_worktree_bundle_repo_folder(bundle, repo)
            for i in range(commits):
                git(folder, "commit", "--allow-empty", "-m", f"work {i}")
            if commits:
                git(folder, "push", "odoo-dev", f"HEAD:refs/heads/{bundle}")
            return git(folder, "rev-parse", "HEAD")

        def push_after(bundle, repo, head):
            folder = commands.get_worktree_bundle_repo_folder(bundle, repo)
            git(folder, "commit", "--allow-empty", "-m", "pushed from elsewhere")
            git(folder, "push", "odoo-dev", f"HEAD:refs/heads/{bundle}")
            pushed = git(folder, "rev-parse", "HEAD")
            git(folder, "reset", "--hard", head)
            return pushed

        def row(head, state="merged", pr_head=None, mine=True):
            pr = {"head": pr_head or head, "mine": mine, "number": 1, "state": state}
            return {"empty": False, "head": head, "pr": pr}

        def empty(head):
            return {"empty": True, "head": head, "pr": None}

        def bundle(name, repos, agent=None, is_open=False):
            return {"agent": agent, "bundle": name, "open": is_open, "repos": repos}

        bundles = []
        head = make("master-same-head", "odoo")
        bundles.append(bundle("master-same-head", {"odoo": row(head)}))
        head = make("master-pr-follows", "odoo")
        pr_head = push_after("master-pr-follows", "odoo", head)
        bundles.append(bundle("master-pr-follows", {"odoo": row(head, pr_head=pr_head)}))
        head = make("master-dev-moved", "odoo")
        push_after("master-dev-moved", "odoo", head)
        bundles.append(bundle("master-dev-moved", {"odoo": row(head)}))
        head = make("master-not-mine", "odoo")
        bundles.append(bundle("master-not-mine", {"odoo": row(head, mine=False)}))
        head = make("master-local-after", "odoo", commits=2)
        pr_head = git(commands.get_repo_folder("odoo"), "rev-parse", f"{head}~1")
        bundles.append(bundle("master-local-after", {"odoo": row(head, pr_head=pr_head)}))
        head = make("master-uncommitted", "odoo")
        folder = commands.get_worktree_bundle_repo_folder("master-uncommitted", "odoo")
        with open(f"{folder}/draft.py", "w") as file:
            file.write("draft\n")
        bundles.append(bundle("master-uncommitted", {"odoo": row(head)}))
        head = make("master-closed", "odoo")
        bundles.append(bundle("master-closed", {"odoo": row(head, state="closed")}))
        head = make("master-with-empty", "odoo")
        base = make("master-with-empty", "enterprise", commits=0)
        bundles.append(bundle("master-with-empty", {"enterprise": empty(base), "odoo": row(head)}))
        head = make("master-other-work", "odoo")
        work = make("master-other-work", "enterprise")
        bundles.append(
            bundle(
                "master-other-work",
                {"enterprise": {"empty": False, "head": work, "pr": None}, "odoo": row(head)},
            ),
        )
        head = make("master-agent-running", "odoo")
        bundles.append(
            bundle("master-agent-running", {"odoo": row(head)}, agent={"state": "running"}),
        )
        head = make("master-window-open", "odoo")
        bundles.append(bundle("master-window-open", {"odoo": row(head)}, is_open=True))
        head = make("master-unknown-pr-head", "odoo")
        bundles.append(
            bundle("master-unknown-pr-head", {"odoo": row(head, pr_head="1" * 40)}),
        )

        output = io.StringIO()
        with redirect_stdout(output):
            deleted, failures = branch_status.delete_merged(bundles)
        dev_deleted = ["master-pr-follows", "master-same-head", "master-with-empty"]
        expected = sorted([*dev_deleted, "master-dev-moved", "master-not-mine"])
        found["bundles deleted"] = (sorted(deleted), expected)
        found["PRs named per deleted bundle"] = (deleted.get("master-same-head"), ["odoo#1"])
        found["failures"] = (failures, [])
        found["output of the deletion"] = (output.getvalue(), "")
        for item in bundles:
            name = item["bundle"]
            found[f"{name}: branches left"] = (
                [
                    repo
                    for repo in item["repos"]
                    if git(commands.get_repo_folder(repo), "branch", "--list", name)
                ],
                [] if name in expected else sorted(item["repos"]),
            )
            found[f"{name}: dev branches left"] = (
                [
                    repo
                    for repo in item["repos"]
                    if git(commands.get_repo_folder(repo), "ls-remote", "--heads", "odoo-dev", name)
                ],
                []
                if name in dev_deleted
                else [repo for repo, data in sorted(item["repos"].items()) if not data["empty"]],
            )
            found[f"{name}: folder left"] = (
                os.path.isdir(commands.get_worktree_bundle_folder(name)),
                name not in expected,
            )
    chain = f"20.0-master-x{branch_status.BUNDLE_SUFFIX}-123-fw"
    for case, author, head, expected in (
        ("my PR", "me", "master-x", True),
        ("fw-bot hop of mine", branch_status.FW_BOT, chain, True),
        ("fw-bot hop of another", branch_status.FW_BOT, "20.0-master-x--other-123-fw", False),
        ("PR of another", "other", "master-x", False),
        ("PR of a deleted account", None, "master-x", False),
    ):
        pr = {"author": author and {"login": author}, "headRefName": head, "viewer": "me"}
        found[f"mine: {case}"] = (branch_status.is_mine(pr), expected)
    failures = [
        f"{case}: {got!r}, expected {expected!r}"
        for case, (got, expected) in found.items()
        if got != expected
    ]
    for line in failures:
        print(line)
    print(f"{len(found) - len(failures)}/{len(found)} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
