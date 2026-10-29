#!/usr/bin/env python3
"""The commits gbs gives the restack of triage, on scratch repos: python3 test_commits.py"""

import os
import subprocess
import sys
import tempfile

import branch_status
import commands

GIT = ["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]


def git(cwd, *args):
    return subprocess.run(
        [*GIT, *args], capture_output=True, check=True, cwd=cwd, text=True
    ).stdout.strip()


def main():
    found = {}
    with tempfile.TemporaryDirectory() as root:
        folder = f"{root}/odoo"
        commands.folder_by_repo.clear()
        commands.folder_by_repo["odoo"] = folder
        commands.WORKTREE_CONTAINER = f"{root}/src"
        git(root, "init", "--bare", "odoo-odoo.git")
        git(root, "init", folder)
        git(folder, "remote", "add", "odoo", f"{root}/odoo-odoo.git")
        git(folder, "commit", "--allow-empty", "-m", "base")
        git(folder, "push", "odoo", "HEAD:refs/heads/master")
        git(folder, "fetch", "odoo")

        def commit(branch, name, text, subject):
            git(folder, "checkout", "-q", branch)
            with open(f"{folder}/{name}", "w") as file:
                file.write(text)
            git(folder, "add", name)
            git(folder, "commit", "-m", subject)
            return git(folder, "rev-parse", "HEAD")

        def read(branch):
            return [
                (commit["sha"], commit["subject"], commit["patch_id"])
                for commit in branch_status.get_commits("odoo", branch)
            ]

        for branch in ("master-prep--me", "master-stacked--me", "master-moved--me", "master-b--me"):
            git(folder, "branch", branch, "odoo/master")
        prep = commit("master-prep--me", "a.py", "a = 1\n", "[FIX] prep")
        old = commit("master-stacked--me", "a.py", "a = 0\n", "[FIX] prep")
        top = commit("master-stacked--me", "b.py", "b = 1\n", "[FIX] top")
        commit("master-moved--me", "c.py", "c = 1\n", "[FIX] other")
        moved = commit("master-moved--me", "a.py", "a = 1\n", "[FIX] prep")
        stacked = read("master-stacked--me")
        found["two commits, newest first"] = [(sha, subject) for sha, subject, _ in stacked]
        found["amended: another patch-id"] = read("master-prep--me")[0][2] != stacked[1][2]
        found["same change on another sha: same patch-id"] = (
            moved != prep and read("master-moved--me")[0][2] == read("master-prep--me")[0][2]
        )
        found["a branch at its base"] = branch_status.get_commits("odoo", "master-b--me")
        branch_status.COMMITS_READ = 1
        found["more than COMMITS_READ"] = branch_status.get_commits("odoo", "master-stacked--me")
        branch_status.COMMITS_READ = 50

        ran = f"{root}/ran"
        program = f"{root}/program"
        with open(program, "w") as file:
            file.write(f'#!/bin/sh\necho "$0 $*" >> {ran}\ncat "$1"\n')
        os.chmod(program, 0o755)
        os.makedirs(f"{folder}/.git/info", exist_ok=True)
        with open(f"{folder}/.git/info/attributes", "w") as file:
            file.write("* diff=bundle\n")
        for key, value in (
            ("diff.bundle.textconv", program),
            ("diff.bundle.command", program),
            ("diff.external", program),
        ):
            git(folder, "config", key, value)
        found["the commits read under a config a bundle wrote"] = read("master-stacked--me")
        found["no program of that config ran"] = not os.path.exists(ran)
        git(folder, "show", "master-stacked--me")
        found["git show runs it"] = os.path.exists(ran)
    expected = {
        "two commits, newest first": [(top, "[FIX] top"), (old, "[FIX] prep")],
        "amended: another patch-id": True,
        "same change on another sha: same patch-id": True,
        "a branch at its base": [],
        "more than COMMITS_READ": None,
        "the commits read under a config a bundle wrote": stacked,
        "no program of that config ran": True,
        "git show runs it": True,
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
