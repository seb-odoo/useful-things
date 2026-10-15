#!/usr/bin/env python3
"""What `bctl delete` holds for hok, what it refuses, and when a yes deletes, on git repos of its
own and without podman: python3 test_delete.py
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon  # noqa: E402
from test_queue import run_client  # noqa: E402

CHILD = "master-child--seb"
CLEAN = "master-clean--seb"
CLOSED = "master-closed-aku"
COMMITTED = "master-committed--seb"
DIRTY = "master-dirty-aku"
MERGED = "master-merged--seb"
MINE = "master-mine--seb"
OPENED = "master-opened-aku"
PUSHED = "master-pushed--seb"
REBASED = "master-rebased-aku"
REFUSED = "master-refused--seb"
REPORT = "blocked: needs opus\n\n- measured"
REPORTED = "master-reported--seb"
STOPPED = "master-stopped--seb"
TYPED = "master-typed--seb"


def git(cwd, *args):
    subprocess.run(["git", *args], capture_output=True, check=True, cwd=cwd)


def commit_as_aku(repo, message):
    git(repo, "-c", "user.name=aku", "-c", "user.email=aku@t", "commit", "--allow-empty", "-qm", message)


def prepare(root):
    """A repo whose master is on both remotes, and a branch per case: the caller, the containers
    that run, the state and the delete_bundle calls the test changes and reads.
    """
    remote, repo = root / "remote.git", root / "odoo"
    git(root, "init", "--quiet", "--bare", str(remote))
    git(root, "init", "--quiet", "--initial-branch=master", str(repo))
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "base")
    for name in ("odoo", "odoo-dev"):
        git(repo, "remote", "add", name, str(remote))
    git(repo, "push", "--quiet", "odoo", "master")
    git(repo, "config", "user.email", "t@t")
    bundles = (
        CHILD,
        CLEAN,
        CLOSED,
        COMMITTED,
        DIRTY,
        MERGED,
        MINE,
        OPENED,
        PUSHED,
        REBASED,
        REFUSED,
        REPORTED,
        STOPPED,
        TYPED,
    )
    for bundle in bundles:
        git(repo, "branch", bundle)
        (root / "master" / bundle).mkdir(parents=True)
    git(repo, "push", "--quiet", "odoo-dev", CLOSED, MINE, OPENED, PUSHED)
    (root / "master" / CHILD / ".agent").mkdir()
    (root / "master" / CHILD / ".agent" / "parent").write_text(f"{CLEAN}\n")
    (root / "transcripts").mkdir()
    typed = [("user", "why 41/50?"), ("assistant", "because")]
    for bundle, after in ((REPORTED, []), (TYPED, typed)):
        agent = root / "master" / bundle / ".agent"
        agent.mkdir()
        for name, text in (("done", "0"), ("result.md", REPORT), ("session", bundle)):
            (agent / name).write_text(f"{text}\n")
        entries = [
            {"type": "user", "message": {"content": "measure it"}},
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "t"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": REPORT}]}},
            {"type": "system", "subtype": "turn_duration"},
            *(
                {"type": author, "message": {"content": [{"type": "text", "text": text}]}}
                for author, text in after
            ),
        ]
        (root / "transcripts" / f"{bundle}.jsonl").write_text(
            "".join(f"{json.dumps(entry)}\n" for entry in entries),
        )
    for bundle in (DIRTY, REBASED):
        git(repo, "checkout", "--quiet", bundle)
        commit_as_aku(repo, "rebased")
        git(repo, "push", "--quiet", "odoo-dev", bundle)
        git(repo, "reset", "--quiet", "--hard", "master")
        commit_as_aku(repo, "before the rebase")
    git(repo, "checkout", "--quiet", MINE)
    git(repo, "-c", "user.name=t", "commit", "--allow-empty", "-qm", "after the PR")
    for branch, message in ((MERGED, "fix"), ("master", "fix, as merged")):
        git(repo, "checkout", "--quiet", branch)
        (repo / "fix.txt").write_text("fix\n")
        git(repo, "add", "fix.txt")
        git(repo, "-c", "user.name=t", "commit", "-qm", message)
    git(repo, "push", "--quiet", "odoo", "master")
    git(repo, "fetch", "--quiet", "--all")
    git(repo, "checkout", "--quiet", COMMITTED)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "own")
    git(repo, "worktree", "add", "--quiet", str(root / "master" / DIRTY / "odoo"), DIRTY)
    (root / "master" / DIRTY / "odoo" / "notes.txt").write_text("review notes\n")
    world = {
        "caller": CLEAN,
        "calls": [],
        "prs": {
            CLOSED: {"odoo/odoo#1": "closed"},
            DIRTY: {"odoo/odoo#4": "closed"},
            MINE: {"odoo/odoo#5": "closed"},
            OPENED: {"odoo/odoo#3": "open"},
            REBASED: {"odoo/odoo#2": "closed"},
        },
        "closed": [],
        "running": {str(root / "master" / CLEAN)},
        "state": {"queue": [{"bundle": REPORTED, "parent": CLEAN}]},
    }
    daemon.HELD_DIR = root / "held"
    daemon.STATE = root
    daemon.close_window = world["closed"].append
    daemon.get_transcript = lambda session: root / "transcripts" / f"{session}.jsonl"
    daemon.get_window_pids = lambda bundle: [1] if bundle in (REPORTED, TYPED) else []
    daemon.get_agent_folder = lambda bundle: root / "master" / bundle / ".agent"
    daemon.get_branch_prs = lambda repo_name, branch: world["prs"].get(branch, {})
    daemon.get_open_windows = lambda: set(world["running"])
    daemon.get_repo_folder = lambda repo_name: str(repo)
    daemon.get_repos = lambda: ["odoo"]
    daemon.get_worktree_bundle_folder = lambda bundle: str(root / "master" / bundle)
    daemon.get_worktree_bundle_repo_folder = lambda bundle, repo_name: str(
        root / "master" / bundle / repo_name,
    )
    daemon.identify = lambda connection: world["caller"]
    daemon.is_in_container = lambda connection: world["caller"] != daemon.HOST
    daemon.read_state = lambda: world["state"]
    daemon.run_script = lambda log, script, *args: world["calls"].append((script, *args))
    daemon.write_state = lambda state: None
    return world


def answer_yes(number):
    """What hok does on a yes: the answer beside the request, then the outcome the daemon left."""
    path = daemon.HELD_DIR / f"{number}.json"
    item = daemon.HELD.read_json(path)
    answer = path.with_suffix(".answer")
    answer.write_text(f"yes {item['digest']}\n")
    daemon.HELD.take(answer, daemon.send_deletion)
    return daemon.HELD.read_json(path.with_suffix(".result"))


def answer_later(bundle, words):
    """What hok does once the request for `bundle` is listed, while a client waits for it."""

    def answer():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for path in daemon.HELD_DIR.glob("*.json"):
                item = daemon.HELD.read_json(path)
                if item and item["send"] == {"bundle": bundle}:
                    reply = path.with_suffix(".answer")
                    reply.write_text(f"yes {item['digest']}\n" if words == "yes" else f"{words}\n")
                    daemon.HELD.take(reply, daemon.send_deletion)
                    return
            time.sleep(0.1)

    threading.Thread(target=answer, daemon=True).start()


def wait_jobs():
    deadline = time.monotonic() + 10
    while any(job["state"] in ("waiting", "running") for job in daemon.jobs.values()):
        if time.monotonic() > deadline:
            break
        time.sleep(0.1)


def check(root):
    world = prepare(root)
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    code, lines = run_client(path, "delete", "--reason", "moved to 19.0")
    checks.append(
        (
            "a bundle asks for its own deletion",
            (code, lines)
            == (0, [f"waits for Seb: hok b1. His yes deletes {CLEAN} once its container stopped."]),
        ),
    )
    item = daemon.HELD.read_json(daemon.HELD_DIR / "1.json")
    checks.append(
        (
            "hok lists what goes",
            (item["title"], item["lines"][1:], item["reason"], item["send"])
            == (
                f"delete the bundle {CLEAN}",
                [
                    "the branch in odoo: no commit of its own, never pushed",
                    "its container, and the databases named after it",
                ],
                "moved to 19.0",
                {"bundle": CLEAN},
            ),
        ),
    )
    code, lines = run_client(path, "delete", "--reason", "moved to 19.0", "--wait", "1")
    checks.append(
        (
            "--wait takes back the request that waits, and exits 75 while it waits",
            (code, lines[1:]) == (75, ["it still waits for Seb's yes"]) and "hok b1." in lines[0],
        ),
    )
    code, lines = run_client(path, "delete", STOPPED, "--reason", "r")
    checks.append(
        (
            "a bundle cannot ask for another one",
            (code, lines)
            == (1, ["bctl: 409 a bundle asks for the deletion of itself or of its children only"]),
        ),
    )
    code, lines = run_client(path, "delete", CHILD, "--reason", "measured, nothing to keep")
    checks.append(
        (
            "a bundle asks for the deletion of a bundle whose last task it queued",
            (code, lines)
            == (0, [f"waits for Seb: hok b2. His yes deletes {CHILD} once its container stopped."]),
        ),
    )
    world["caller"] = daemon.HOST
    code, lines = run_client(path, "delete", COMMITTED, "--reason", "r")
    checks.append(
        (
            "a commit no remote has is kept",
            (code, lines) == (1, [f"bctl: 409 {COMMITTED} has commits in odoo that no remote has"]),
        ),
    )
    code, lines = run_client(path, "delete", PUSHED, "--reason", "r")
    checks.append(
        (
            "a pushed branch is kept",
            (code, lines) == (1, [f"bctl: 409 {PUSHED} is pushed in odoo, a PR may use it"]),
        ),
    )
    code, lines = run_client(path, "delete", OPENED, "--reason", "r")
    checks.append(
        (
            "a branch with an open PR is kept",
            (code, lines) == (1, [f"bctl: 409 {OPENED} has an open PR in odoo: odoo/odoo#3"]),
        ),
    )
    code, lines = run_client(path, "delete", MINE, "--reason", "r")
    checks.append(
        (
            "a commit of Seb no remote has is kept, even once the PR is closed",
            (code, lines) == (1, [f"bctl: 409 {MINE} has commits in odoo that no remote has"]),
        ),
    )
    code, lines = run_client(path, "delete", DIRTY, "--reason", "r")
    checks.append(
        (
            "a file not committed is kept when the deletion needs --force",
            (code, lines) == (1, [f"bctl: 409 {DIRTY} has uncommitted files in odoo"]),
        ),
    )
    code, lines = run_client(path, "delete", "master", "--reason", "r")
    checks.append(
        ("a base is no bundle", (code, lines) == (1, ["bctl: 400 not a bundle name: master"])),
    )
    result = answer_yes(1)
    checks.append(
        (
            "a yes while the container runs waits for its stop",
            (result["code"], result["text"], world["calls"], world["state"]["deletions"])
            == (
                0,
                f"{CLEAN} is deleted once its container stops: close its window or its terminal",
                [],
                [CLEAN],
            ),
        ),
    )
    world["running"].clear()
    daemon.delete_stopped()
    wait_jobs()
    checks.append(
        (
            "the stop of the container deletes it",
            (world["calls"], world["state"]["deletions"])
            == ([("delete_bundle.py", CLEAN)], []),
        ),
    )
    answer_later(REFUSED, "no it still runs")
    code, lines = run_client(path, "delete", REFUSED, "--reason", "r", "--wait", "10")
    checks.append(
        (
            "--wait prints his refusal and exits 126",
            (code, lines[1:], len(world["calls"]))
            == (126, ["Seb refused it, and said: it still runs. Nothing was sent."], 1),
        ),
    )
    answer_later(STOPPED, "yes")
    code, lines = run_client(path, "delete", STOPPED, "--reason", "r", "--wait", "10")
    checks.append(
        (
            "a yes on a stopped container deletes it at once, and --wait prints it",
            (code, lines[1:], world["calls"][1:])
            == (0, [f"{STOPPED} deleted"], [("delete_bundle.py", STOPPED)]),
        ),
    )
    code, lines = run_client(path, "delete", CLEAN, "--reason", "r")
    git(root / "odoo", "branch", "--quiet", "--force", CLEAN, COMMITTED)
    result = answer_yes(int(lines[0].partition("hok b")[2].partition(".")[0]))
    checks.append(
        (
            "a commit made before the yes keeps the bundle",
            (result["code"], result["text"], len(world["calls"]))
            == (1, f"{CLEAN} has commits in odoo that no remote has", 2),
        ),
    )
    for name, bundle, line, call in (
        (
            "a branch whose PRs are closed goes",
            CLOSED,
            "odoo/odoo#1 closed",
            ("delete_bundle.py", CLOSED),
        ),
        (
            "the commits of the PR author no remote has go, with --force",
            REBASED,
            "odoo/odoo#2 closed, commits of aku@t that no remote has",
            ("delete_bundle.py", "--force", REBASED),
        ),
        (
            "a commit of Seb whose change master has under another sha goes, with --force",
            MERGED,
            "its commits are in master, commits of t@t that no remote has",
            ("delete_bundle.py", "--force", MERGED),
        ),
    ):
        code, lines = run_client(path, "delete", bundle, "--reason", "r")
        number = int(lines[0].partition("hok b")[2].partition(".")[0])
        item = daemon.HELD.read_json(daemon.HELD_DIR / f"{number}.json")
        result = answer_yes(number)
        checks.append(
            (
                name,
                (item["lines"][1], result["text"], world["calls"][-1])
                == (f"the branch in odoo: {line}", f"{bundle} deleted", call),
            ),
        )
    world["running"].update(str(root / "master" / bundle) for bundle in (REPORTED, TYPED))
    code, lines = run_client(path, "delete", REPORTED, "--reason", "r")
    number = int(lines[0].partition("hok b")[2].partition(".")[0])
    item = daemon.HELD.read_json(daemon.HELD_DIR / f"{number}.json")
    checks.append(
        (
            "the request shows the report its session ended on, and its yes closes the window",
            (lines, item["lines"][3:], item["yes"])
            == (
                [
                    f"waits for Seb: hok b{number}. His yes deletes {REPORTED} once its agent"
                    " window closed.",
                ],
                [
                    f"its queued task, from {CLEAN}",
                    "its agent window, which ends on this report:",
                    "  blocked: needs opus",
                    "",
                    "  - measured",
                ],
                "its agent window closes, then delete_bundle deletes it",
            ),
        ),
    )
    result = answer_yes(number)
    checks.append(
        (
            "the yes closes the window and drops the queued task",
            (result["text"], world["closed"], world["state"]["queue"], world["state"]["deletions"])
            == (f"{REPORTED} is deleted once its agent window closed", [REPORTED], [], [REPORTED]),
        ),
    )
    code, lines = run_client(path, "delete", TYPED, "--reason", "r")
    number = int(lines[0].partition("hok b")[2].partition(".")[0])
    item = daemon.HELD.read_json(daemon.HELD_DIR / f"{number}.json")
    result = answer_yes(number)
    checks.append(
        (
            "a session that went on after its report keeps its window for Seb",
            (lines[0].endswith("once its container stopped."), len(item["lines"]), result["text"])
            == (
                True,
                3,
                f"{TYPED} is deleted once its container stops: close its window or its terminal",
            )
            and world["closed"] == [REPORTED],
        ),
    )
    server.shutdown()
    server.server_close()
    return [name for name, passed in checks if not passed], len(checks)


def check_reviews(root):
    """Which bundles drop_idle_reviews deletes, from the dates of the files it reads and of the
    stop of their container.
    """
    now = time.time()
    old, recent, closed, shut = now - 3 * 86400, now - 86400, now - 20 * 60, now - 5 * 60
    calls, world = [], {"building": [], "closed": {}, "open": set(), "queue": []}
    bundles = (
        "master-building-aku",
        "master-closed-aku",
        "master-commit-aku",
        "master-fixed-aku",
        "master-kept-aku",
        "master-later-aku",
        "master-mine--seb",
        "master-old-aku",
        "master-open-aku",
        "master-queued-aku",
        "master-rebased-aku",
        "master-recent-aku",
        "master-reopened-aku",
        "master-shut-aku",
        "master-tab-aku",
    )
    repo = root / "odoo"
    git(root, "init", "--quiet", "--bare", str(root / "remote.git"))
    git(root, "init", "--quiet", "--initial-branch=master", str(repo))
    git(repo, "config", "user.email", "t@t")
    git(repo, "-c", "user.name=t", "commit", "--allow-empty", "-qm", "base")
    git(repo, "remote", "add", "odoo", str(root / "remote.git"))
    git(repo, "push", "--quiet", "odoo", "master")
    git(repo, "checkout", "--quiet", "-b", "master-rebased-aku")
    commit_as_aku(repo, "before the rebase")
    git(repo, "checkout", "--quiet", "-b", "master-fixed-aku", "master")
    git(repo, "-c", "user.name=t", "commit", "--allow-empty", "-qm", "fix of the review")
    for bundle in bundles:
        folder = root / "master" / bundle
        (folder / "odoo").mkdir(parents=True)
        (folder / "odoo" / ".git").write_text(f"gitdir: {root / 'wt' / bundle}\n")
        (root / "wt" / bundle / "logs").mkdir(parents=True)
        (root / "claude-config" / bundle).mkdir(parents=True)
        for path in (
            folder / daemon.MADE,
            root / "wt" / bundle / "logs" / "HEAD",
            root / "claude-config" / bundle / ".claude.json",
        ):
            path.touch()
            os.utime(path, (old, old))
    os.utime(root / "master" / "master-recent-aku" / daemon.MADE, (recent, recent))
    os.utime(root / "wt" / "master-commit-aku" / "logs" / "HEAD", (recent, recent))
    os.utime(root / "claude-config" / "master-tab-aku" / ".claude.json", (recent, recent))
    for bundle, stop in (
        ("master-building-aku", closed),
        ("master-closed-aku", closed),
        ("master-reopened-aku", closed),
        ("master-shut-aku", shut),
    ):
        os.utime(root / "master" / bundle / daemon.MADE, (recent, recent))
        world["closed"][str(root / "master" / bundle)] = stop
    os.utime(root / "master" / "master-later-aku" / daemon.MADE, (recent, recent))
    os.utime(root / "claude-config" / "master-reopened-aku" / ".claude.json", (now - 120, now - 120))
    world["building"].append("master-building-aku")
    world["open"].add(str(root / "master" / "master-open-aku"))
    world["queue"].append({"bundle": "master-queued-aku"})

    def run_script(log, script, *args):
        calls.append(args)
        if args[-1] == "master-kept-aku":
            raise daemon.Refused("delete_bundle.py exited with 1")
        shutil.rmtree(root / "master" / args[-1])

    daemon.CLAUDE_CONFIG_CONTAINER = str(root / "claude-config")
    daemon.STATE = root
    daemon.get_closed_windows = lambda: dict(world["closed"])
    daemon.get_open_windows = lambda: set(world["open"])
    daemon.get_repo_folder = lambda repo_name: str(repo)
    daemon.get_repos = lambda: ["odoo"]
    daemon.get_worktree_bundle_folder = lambda bundle: str(root / "master" / bundle)
    daemon.get_worktree_bundle_repo_folder = lambda bundle, repo_name: str(
        root / "master" / bundle / repo_name,
    )
    daemon.get_worktree_container_folder = lambda: str(root)
    daemon.read_state = lambda: {"building": world["building"], "queue": world["queue"]}
    daemon.run_script = run_script
    checks = []
    daemon.drop_idle_reviews()
    wait_jobs()
    checks.append(
        (
            "a bundle of others unused for 2 days goes, not one of Seb, open, queued or used since",
            {("master-kept-aku",), ("master-old-aku",)} <= set(calls),
        ),
    )
    checks.append(
        (
            "the older head of a rebased PR goes with --force, a commit of Seb keeps the bundle",
            ("--force", "master-rebased-aku") in calls,
        ),
    )
    checks.append(
        (
            "a bundle of others goes 10 minutes after its container stopped, not one being built",
            ("master-closed-aku",) in calls and len(calls) == 4,
        ),
    )
    calls.clear()
    daemon.drop_idle_reviews()
    wait_jobs()
    checks.append(("one delete_bundle kept is not tried again", calls == []))
    (root / "master" / "master-kept-aku" / ".agent").mkdir()
    (root / "master" / "master-kept-aku" / ".agent" / "task.md").touch()
    daemon.drop_idle_reviews()
    os.utime(root / "master" / "master-kept-aku" / ".agent" / "task.md", (old + 1, old + 1))
    daemon.drop_idle_reviews()
    wait_jobs()
    checks.append(
        ("it is tried again once used, and unused for 2 days", calls == [("master-kept-aku",)]),
    )
    calls.clear()
    later = "master-later-aku"
    world["closed"][str(root / "master" / later)] = closed
    with daemon.work_lock:
        daemon.drop_idle_reviews()
        daemon.drop_idle_reviews()
        deletions = [job for job in daemon.jobs.values() if job["verb"] == f"delete {later}"]
        checks.append(("a second check starts no second deletion", len(deletions) == 1))
        (root / "claude-config" / later / ".claude.json").touch()
    wait_jobs()
    checks.append(("a bundle used while its deletion waited stays", calls == []))
    return [name for name, passed in checks if not passed], len(checks)


def main():
    with tempfile.TemporaryDirectory() as root:
        failures, total = check(pathlib.Path(root))
    with tempfile.TemporaryDirectory() as root:
        more, count = check_reviews(pathlib.Path(root))
        failures, total = failures + more, total + count
    for name in failures:
        print(f"failed: {name}")
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
