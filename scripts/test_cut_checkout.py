#!/usr/bin/env python3
"""A build asked again over a worktree whose checkout a kill cut, on scratch repos:
python3 test_cut_checkout.py"""

import os
import pathlib
import subprocess
import sys
import tempfile

import commands
from rich.console import Console
from rich.live import Live
from utils import UtilsRunner

REPO = "odoo"
BUNDLE = "master-cut--me"
GIT = ["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]


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
        git(root, "init", "--bare", "odoo.git")
        git(repo, "remote", "add", "odoo", f"{root}/odoo.git")
        pathlib.Path(repo, "file").write_text("base\n")
        git(repo, "add", "file")
        git(repo, "commit", "-m", "base")
        git(repo, "push", "odoo", "HEAD:refs/heads/master")
        git(repo, "fetch", "odoo")
        os.makedirs(commands.get_worktree_bundle_folder(BUNDLE))
        folder = pathlib.Path(commands.get_worktree_bundle_repo_folder(BUNDLE, REPO))

        def build():
            try:
                runner.add_worktree(
                    repo=REPO,
                    bundle_name=BUNDLE,
                    make_branch=True,
                    target_ref="odoo/master",
                    on_existing=lambda runner: runner.switch_to_branch(
                        repo=REPO, branch=BUNDLE, target_ref="odoo/master"
                    ),
                )
            except subprocess.CalledProcessError as error:
                return error.stderr or str(error)
            return (git(folder, "status", "--short"), git(folder, "branch", "--show-current"))

        def gitdir():
            return pathlib.Path((folder / ".git").read_text().removeprefix("gitdir:").strip())

        build()
        (gitdir() / "index").unlink()
        found["a checkout cut before its index is done again"] = (build(), ("", BUNDLE))
        gone = gitdir().parent / "gone"
        git(repo, "worktree", "remove", "--force", "--force", str(folder))
        folder.mkdir()
        (folder / "file").write_text("base\n")
        (folder / ".git").write_text(f"gitdir: {gone}\n")
        found["a checkout cut before its gitdir is done again"] = (build(), ("", BUNDLE))
        (folder / "notes").write_text("mine\n")
        found["a whole worktree is kept"] = (
            (build(), (folder / "notes").read_text()),
            (("?? notes", BUNDLE), "mine\n"),
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
