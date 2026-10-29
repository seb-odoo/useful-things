#!/usr/bin/env python3
"""Which branches GET /status reads again with git, on a throwaway repo:
python3 test_status.py
"""

import os
import pathlib
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "scripts")]

import branch_status  # noqa: E402

import daemon  # noqa: E402

BASE = "refs/remotes/odoo/master"
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
# Set by the ops container, where git with no config read trusts no repo.
ENV.pop("GIT_TEST_ASSUME_DIFFERENT_OWNER", None)
NAME = ("-c", "user.name=t", "-c", "user.email=t@t")


def check(root):
    repo = root / "odoo"
    calls = []

    def git(*args):
        command = ["git", *NAME, *args]
        subprocess.run(command, capture_output=True, check=True, cwd=repo, env=ENV)

    def commit(name):
        (repo / name).write_text(name)
        git("add", name)
        git("commit", "-qm", name)

    def get_status(name, bundle, write_tree):
        calls.append(bundle)
        return status(name, bundle, write_tree)

    def read():
        calls.clear()
        heads = {("odoo", name): head for name, _date, head, _path in get_branches("odoo")}
        heads.pop(("odoo", "main"))
        with ThreadPoolExecutor() as executor:
            said = daemon.read_statuses(executor, heads, daemon.has_write_tree())
        counts = {bundle: s and (s["ahead"], s["behind"]) for (_repo, bundle), s in said.items()}
        return counts, sorted(calls)

    repo.mkdir()
    git("init", "-q", "-b", "main")
    commit("base")
    git("update-ref", BASE, "HEAD")
    for branch in ("master-a--seb", "master-b--seb", "17.0-c--seb"):
        git("branch", branch)
    git("switch", "-q", "master-a--seb")
    commit("a")
    branch_status.get_repo_folder = lambda name: str(root / name)
    get_branches, status = branch_status.get_local_branches, daemon.get_status
    daemon.get_status = get_status
    checks = []
    expected = {"17.0-c--seb": None, "master-a--seb": (1, 0), "master-b--seb": (0, 0)}
    checks.append(("a first read runs git on each branch", read() == (expected, sorted(expected))))
    checks.append(("a read where nothing moved runs no git", read() == (expected, [])))
    git("switch", "-q", "master-b--seb")
    commit("b")
    expected["master-b--seb"] = (1, 0)
    checks.append(("a commit reads its branch again", read() == (expected, ["master-b--seb"])))
    git("switch", "-q", "main")
    commit("main")
    git("update-ref", BASE, "HEAD")
    expected.update({"master-a--seb": (1, 1), "master-b--seb": (1, 1)})
    both = ["master-a--seb", "master-b--seb"]
    checks.append(("a fetched base reads its branches again", read() == (expected, both)))
    git("branch", "-qD", "master-b--seb")
    del expected["master-b--seb"]
    checks.append(("a deleted branch leaves the status", read() == (expected, [])))
    kept = [("odoo", "17.0-c--seb"), ("odoo", "master-a--seb")]
    checks.append(("and leaves the kept statuses", sorted(daemon.git_statuses) == kept))
    git("update-ref", "refs/remotes/odoo/17.0", "HEAD")
    expected["17.0-c--seb"] = (0, 1)
    checks.append(("a base that appears reads its branches", read() == (expected, ["17.0-c--seb"])))
    return [name for name, passed in checks if not passed], len(checks)


def main():
    with tempfile.TemporaryDirectory() as root:
        failures, total = check(pathlib.Path(root))
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
