#!/usr/bin/env python3
"""repack_repos.py on a scratch repo whose two packs share objects, one of them unreachable:
python3 test_repack_repos.py
"""

import glob
import os
import subprocess
import sys
import tempfile

GIT = ["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]
HERE = os.path.dirname(os.path.abspath(__file__))


def git(cwd, *args):
    return subprocess.run(
        [*GIT, *args], capture_output=True, check=True, cwd=cwd, text=True
    ).stdout.strip()


def objects(repo):
    names = set()
    for index in glob.glob(f"{repo}/.git/objects/pack/*.idx"):
        with open(index, "rb") as file:
            out = subprocess.run(
                ["git", "show-index"], stdin=file, capture_output=True, check=True, text=True
            )
        names |= {line.split()[1] for line in out.stdout.splitlines()}
    return len(glob.glob(f"{repo}/.git/objects/pack/*.pack")), len(names)


def main():
    with tempfile.TemporaryDirectory() as root:
        repo = f"{root}/repo/odoo"
        git(root, "init", "--quiet", repo)
        git(repo, "commit", "--quiet", "--allow-empty", "-m", "first")
        git(repo, "checkout", "--quiet", "-b", "gone")
        git(repo, "commit", "--quiet", "--allow-empty", "-m", "gone")
        git(repo, "checkout", "--quiet", "-")
        git(repo, "repack", "-a", "-q")
        git(repo, "branch", "-D", "gone")
        git(repo, "reflog", "expire", "--expire=now", "--all")
        git(repo, "commit", "--quiet", "--allow-empty", "-m", "second")
        git(repo, "repack", "-a", "-q")
        before = objects(repo)
        res = subprocess.run(
            [sys.executable, "repack_repos.py"],
            capture_output=True,
            check=False,
            cwd=HERE,
            env=dict(os.environ, REPO_ROOT=f"{root}/repo"),
            text=True,
        )
        checks = [
            ("the repo starts with two packs", before[0] == 2),
            (
                "one pack is left, with every object, the unreachable one too",
                objects(repo) == (1, before[1]),
            ),
            (
                "the other repos are not there and are skipped",
                res.stdout.startswith("odoo: exit 0,"),
            ),
        ]
    failures = [name for name, passed in checks if not passed]
    for line in failures:
        print(line)
    print(f"{len(checks) - len(failures)}/{len(checks)} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
