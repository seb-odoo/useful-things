#!/usr/bin/env python3
"""The upstream a bundle branch gets, on scratch repos: python3 test_upstream.py"""

import os
import pathlib
import subprocess
import sys
import tempfile

import commands
import upstream
from rich.console import Console
from rich.live import Live
from utils import UtilsRunner

REPO = "odoo"
NEW = "master-new--me"
FETCHED = "master-fetched--me"
REMOTES = ("odoo", "odoo-dev")
GIT =["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]


def git(cwd, *args):
    return subprocess.run(
        [*GIT, *args], capture_output=True, check=True, cwd=cwd, text=True
    ).stdout.strip()


def main():
    runner = UtilsRunner(live=Live("", auto_refresh=False, console=Console(quiet=True)))
    found = {}
    with tempfile.TemporaryDirectory() as root:
        repo = f"{root}/repo"
        commands.folder_by_repo[REPO] = repo
        commands.WORKTREE_CONTAINER = f"{root}/src"
        git(root, "init", repo)
        for remote in REMOTES:
            git(root, "init", "--bare", f"{remote}.git")
            git(repo, "remote", "add", remote, f"{root}/{remote}.git")
        git(repo, "config", "push.default", "simple")
        git(repo, "commit", "--allow-empty", "-m", "base")
        git(repo, "push", "odoo", "HEAD:refs/heads/master")
        git(repo, "push", "odoo-dev", f"HEAD:refs/heads/{FETCHED}")
        git(repo, "fetch", "--all")

        def upstream_of(branch):
            return git(repo, "for-each-ref", "--format=%(upstream:short)", f"refs/heads/{branch}")

        for bundle in (NEW, FETCHED):
            os.makedirs(commands.get_worktree_bundle_folder(bundle))
        folder = commands.get_worktree_bundle_repo_folder(NEW, REPO)
        runner.add_worktree(repo=REPO, bundle_name=NEW, make_branch=True, target_ref="odoo/master")
        found["upstream of a branch created from the base"] = (upstream_of(NEW), "")
        runner.switch_to_branch(repo=REPO, branch=NEW, target_ref="odoo/master")
        found["upstream of a branch reset on the base"] = (upstream_of(NEW), "")
        runner.set_push_remote(repo=REPO, branch=NEW)
        found["the start sets the upstream of a new branch"] = (
            (upstream.set_upstream(REPO, NEW), upstream_of(NEW)),
            (True, f"odoo-dev/{NEW}"),
        )
        config = pathlib.Path(repo, ".git", "config")
        before = (config.stat().st_ino, config.stat().st_mtime_ns, config.read_text())
        git(folder, "commit", "--allow-empty", "-m", "work")
        git(folder, "push")
        after = (config.stat().st_ino, config.stat().st_mtime_ns, config.read_text())
        found["a plain push then writes no config, read-only in a bundle"] = (after, before)
        found["upstream after a plain push"] = (upstream_of(NEW), f"odoo-dev/{NEW}")
        found["remotes holding it after a plain push"] = (
            [remote for remote in REMOTES if git(repo, "ls-remote", "--heads", remote, NEW)],
            ["odoo-dev"],
        )
        runner.add_worktree(
            repo=REPO,
            bundle_name=FETCHED,
            make_branch=True,
            target_ref=f"odoo-dev/{FETCHED}",
            track=True,
        )
        found["upstream of a branch fetched from the dev remote"] = (
            upstream_of(FETCHED),
            f"odoo-dev/{FETCHED}",
        )
        git(repo, "branch", "--set-upstream-to", "odoo/master", FETCHED)
        found["the start keeps an upstream set"] = (
            (upstream.set_upstream(REPO, FETCHED), upstream_of(FETCHED)),
            (False, "odoo/master"),
        )
        found["and makes no branch"] = (
            (upstream.set_upstream(REPO, "master-none--me"), upstream_of("master-none--me")),
            (False, ""),
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
