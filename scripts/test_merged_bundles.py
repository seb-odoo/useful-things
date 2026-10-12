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

        def make(bundle, repo, commits=1):
            os.makedirs(commands.get_worktree_bundle_folder(bundle), exist_ok=True)
            runner.add_worktree(
                repo=repo, bundle_name=bundle, make_branch=True, target_ref="odoo/master"
            )
            folder = commands.get_worktree_bundle_repo_folder(bundle, repo)
            for i in range(commits):
                git(folder, "commit", "--allow-empty", "-m", f"work {i}")
            return git(folder, "rev-parse", "HEAD")

        def row(head, state="merged", pr_head=None):
            pr = {"head": pr_head or head, "number": 1, "state": state}
            return {"empty": False, "head": head, "pr": pr}

        def empty(head):
            return {"empty": True, "head": head, "pr": None}

        def bundle(name, repos, agent=None, is_open=False):
            return {"agent": agent, "bundle": name, "open": is_open, "repos": repos}

        bundles = []
        head = make("master-same-head", "odoo")
        bundles.append(bundle("master-same-head", {"odoo": row(head)}))
        head = make("master-pr-follows", "odoo")
        folder = commands.get_worktree_bundle_repo_folder("master-pr-follows", "odoo")
        git(folder, "commit", "--allow-empty", "-m", "pushed from elsewhere")
        pr_head = git(folder, "rev-parse", "HEAD")
        git(folder, "reset", "--hard", head)
        bundles.append(bundle("master-pr-follows", {"odoo": row(head, pr_head=pr_head)}))
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
            deleted, failure = branch_status.delete_merged(bundles)
        expected = ["master-pr-follows", "master-same-head", "master-with-empty"]
        found["bundles deleted"] = (sorted(deleted), expected)
        found["PRs named per deleted bundle"] = (deleted.get("master-same-head"), ["odoo#1"])
        found["failure"] = (failure, None)
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
            found[f"{name}: folder left"] = (
                os.path.isdir(commands.get_worktree_bundle_folder(name)),
                name not in expected,
            )
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
