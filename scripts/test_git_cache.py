#!/usr/bin/env python3
"""When gbs asks git again for the status, the push and the commits of a branch, on scratch repos:
python3 test_git_cache.py"""

import pathlib
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

import branch_status
import commands

GIT = ["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]


def git(cwd, *args):
    return subprocess.run(
        [*GIT, *args], capture_output=True, check=True, cwd=cwd, text=True
    ).stdout.strip()


def main():
    found = {}
    computed = []
    for name in ("get_status", "get_push", "get_commits"):
        real = getattr(branch_status, name)

        def counted(*args, real=real, name=name):
            computed.append((name, args[1]))
            return real(*args)

        setattr(branch_status, name, counted)
    with tempfile.TemporaryDirectory() as root:
        folder = f"{root}/odoo"
        commands.folder_by_repo.clear()
        commands.folder_by_repo["odoo"] = folder
        branch_status.GIT_CACHE = pathlib.Path(root, "branch-status.json")
        git(root, "init", "--bare", "odoo-odoo.git")
        git(root, "init", "--bare", "odoo-dev.git")
        git(root, "init", folder)
        git(folder, "remote", "add", "odoo", f"{root}/odoo-odoo.git")
        git(folder, "remote", "add", "odoo-dev", f"{root}/odoo-dev.git")
        git(folder, "commit", "--allow-empty", "-m", "base")
        git(folder, "push", "odoo", "HEAD:refs/heads/master")
        git(folder, "fetch", "odoo")
        for branch in ("master-a--me", "master-b--me"):
            git(folder, "branch", branch, "odoo/master")
            git(folder, "checkout", "-q", branch)
            git(folder, "commit", "--allow-empty", "-m", f"[FIX] {branch}")
        git(folder, "push", "odoo-dev", "master-a--me")
        git(folder, "fetch", "odoo-dev")

        def read():
            computed.clear()
            heads = {
                ("odoo", branch): git(folder, "rev-parse", branch)
                for branch in ("master-a--me", "master-b--me")
            }
            with ThreadPoolExecutor() as executor:
                facts = branch_status.read_git_facts(executor, heads, True)
            return {pair[1]: (fact["status"], fact["push"]) for pair, fact in facts.items()}

        first = read()
        found["the first read asks git for each branch"] = len(computed)
        found["a branch pushed, one not"] = (first["master-a--me"][1], first["master-b--me"][1])
        found["nothing moved: no git"] = (read() == first, len(computed))
        git(folder, "checkout", "-q", "master-b--me")
        git(folder, "commit", "--allow-empty", "-m", "[FIX] more")
        read()
        found["a new commit: that branch only"] = sorted({branch for _, branch in computed})
        git(folder, "checkout", "-q", "--detach", "odoo/master")
        git(folder, "commit", "--allow-empty", "-m", "[FIX] on master")
        git(folder, "push", "odoo", "HEAD:refs/heads/master")
        git(folder, "fetch", "odoo")
        moved = read()
        found["the base moved: both, now behind"] = (
            len(computed),
            [moved[branch][0]["behind"] for branch in ("master-a--me", "master-b--me")],
        )
        git(folder, "push", "odoo-dev", "master-b--me")
        git(folder, "fetch", "odoo-dev")
        read()
        found["a push: that branch only"] = sorted({branch for _, branch in computed})
        branch_status.GIT_CACHE.write_text("[1, 2")
        read()
        found["a broken cache: git again"] = len(computed)
    expected = {
        "the first read asks git for each branch": 6,
        "a branch pushed, one not": ({"ahead": 0, "behind": 0}, None),
        "nothing moved: no git": (True, 0),
        "a new commit: that branch only": ["master-b--me"],
        "the base moved: both, now behind": (6, [1, 1]),
        "a push: that branch only": ["master-b--me"],
        "a broken cache: git again": 6,
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
