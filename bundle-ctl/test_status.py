#!/usr/bin/env python3
"""Which branches GET /status reads again with git, on a throwaway repo, and the agent it gives
a bundle whose agent runs on a worker:
python3 test_status.py
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "scripts")]

import branch_status  # noqa: E402

import daemon  # noqa: E402

BASE = "refs/remotes/odoo/master"
SESSION = "8b9b2f6e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
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


def check_remote(root):
    agent = root / "bundles" / "master-a--seb" / ".agent"
    agent.mkdir(parents=True)
    alive = {}

    def get_agent(bundle, head_date, is_alive):
        alive[bundle] = is_alive
        return {"state": "running" if is_alive else "stopped"}

    stubs = {
        name: getattr(daemon, name)
        for name in (
            "get_agent",
            "get_agent_folder",
            "get_open_bundle_folders",
            "get_repo_folder",
            "get_repos",
            "get_worktree_bundle_folder",
            "read_state",
        )
    }
    daemon.get_agent = get_agent
    daemon.get_open_bundle_folders = set
    daemon.get_repo_folder = lambda name: str(root / name)
    daemon.get_repos = lambda: ["odoo"]
    daemon.get_worktree_bundle_folder = lambda bundle: str(root / "bundles" / bundle)
    daemon.read_state = lambda: {"launches": {}, "queue": []}

    def read():
        rows = {row["bundle"]: row["agent"] for row in daemon.list_bundles()}
        return alive["master-a--seb"], rows["master-a--seb"].get("where")

    checks = []
    try:
        checks.append(("an agent with no window and no worker is stopped", read() == (False, None)))
        (agent / "remote.json").write_text('{"worker": "sebpc", "job": "1"}')
        daemon.remote_agent.STATE = agent.parent / "state"
        daemon.remote_agent.keep_record("master-a--seb", {"worker": "sebpc", "job": "1"})
        checks.append(("an agent on a worker is alive, with its worker", read() == (True, "sebpc")))
        task = "- ci: odoo#1 ci/x red, https://runbot.odoo.com/1"
        (agent / "task.md").write_text(f"{task}\n")
        daemon.get_agent = lambda bundle, head_date, is_alive: {"state": "done"}
        daemon.get_agent_folder = lambda bundle: root / "bundles" / bundle / ".agent"
        done = {row["bundle"]: row["agent"] for row in daemon.list_bundles()}["master-a--seb"]
        checks.append(("a done agent carries its task, for the triage", done.get("task") == task))
    finally:
        vars(daemon).update(stubs)
    return [name for name, passed in checks if not passed], len(checks)


def check_texts(root):
    """A bundle reads the task and the report of its own agent and of the agents it queued a task
    for, never those of another bundle (security check 11-03, B18). The host reads them all."""
    agent = {"state": "done", "result": "the report", "task": "the task", "parent": "b1"}
    rows = [{"bundle": name, "agent": dict(agent)} for name in ("b1", "b2", "b3")]
    queue = [
        {"bundle": "b2", "parent": "b1", "session": SESSION, "task": "queued by b1"},
        {"bundle": "b3", "parent": "host", "session": SESSION, "task": "queued by the host"},
    ]
    stubs = {
        name: getattr(daemon, name)
        for name in ("count_agents", "describe_queue", "get_open_windows", "read_state", "RUNS")
    }
    daemon.bundle_rows.update(rows=rows, read=float("inf"))
    daemon.count_agents = lambda windows: 0
    daemon.get_open_windows = set
    daemon.describe_queue = lambda state: [dict(item) for item in queue]
    daemon.read_state = lambda: {"launches": {}, "queue": []}
    daemon.RUNS = type("Runs", (), {"describe": lambda self: {}})()
    daemon.PARENTS_DIR = root / "parents"
    daemon.keep_parent("b2", "b1")
    checks = []
    try:
        seen = {}
        for caller in (daemon.HOST, "b1", "b2"):
            answer = daemon.status(caller, {}, {})[1]
            seen[caller] = (
                {row["bundle"]: row["agent"]["result"] for row in answer["bundles"]},
                [item["task"] for item in answer["queue"]],
            )
        report, task = "the report", "queued by b1"
        checks.append(
            (
                "the host reads every text",
                seen[daemon.HOST]
                == ({"b1": report, "b2": report, "b3": report}, [task, "queued by the host"]),
            )
        )
        checks.append(
            (
                "a bundle its own and its child's",
                seen["b1"] == ({"b1": report, "b2": report, "b3": ""}, [task, None]),
            )
        )
        checks.append(
            (
                "and its own task, but no text of another bundle",
                seen["b2"] == ({"b1": "", "b2": report, "b3": ""}, [task, None]),
            )
        )
        gbs = json.dumps({"bundles": rows, "deleted": [], "errors": []})
        ran = subprocess.CompletedProcess([], 0, stdout=gbs, stderr="")
        with mock.patch.object(daemon.subprocess, "run", return_value=ran):
            found = daemon.branches("b2", {}, {})[1]["bundles"]
        checks.append((
            "GET /branches the same",
            [row["agent"]["result"] for row in found] == ["", "the report", ""],
        ))
        found = daemon.get_queue("b3", {}, {})[1]["queue"]
        checks.append(
            (
                "GET /queue the same",
                [item["task"] for item in found] == [None, "queued by the host"],
            )
        )
        found = [
            (item["session"], item["parent"]) for item in daemon.get_queue("b3", {}, {})[1]["queue"]
        ]
        checks.append(
            (
                "nor the session or parent of another bundle's task (B102)",
                found == [(None, None), (SESSION, "host")],
            )
        )
        found = [row["agent"]["parent"] for row in daemon.status("b3", {}, {})[1]["bundles"]]
        checks.append(("nor the parent of another bundle's agent", found == [None, None, "b1"]))
    finally:
        vars(daemon).update(stubs)
        daemon.bundle_rows.update(rows=[], read=0)
    return [name for name, passed in checks if not passed], len(checks)


def main():
    with tempfile.TemporaryDirectory() as root:
        failures, total = check(pathlib.Path(root))
        more, count = check_remote(pathlib.Path(root))
        failures, total = failures + more, total + count
        more, count = check_texts(pathlib.Path(root))
        failures, total = failures + more, total + count
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
