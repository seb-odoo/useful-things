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
DROPPED = "master-dropped--seb"
LEFT = "master-left--seb"
MERGED = "master-merged--seb"
MINE = "master-mine--seb"
OPENED = "master-opened-aku"
PUSHED = "master-pushed--seb"
QUEUED = "master-queued--seb"
REBASED = "master-rebased-aku"
REFUSED = "master-refused--seb"
REPORT = "blocked: needs opus\n\n- measured"
REPORTED = "master-reported--seb"
STOPPED = "master-stopped--seb"
TYPED = "master-typed--seb"
PENDING = (
    "is deleted once its agent ended its turn: its window closes and its container stops then"
)


def waits(bundle):
    return f"His yes closes the window of {bundle} once its agent ended its turn, then deletes it."


def git(cwd, *args):
    subprocess.run(["git", *args], capture_output=True, check=True, cwd=cwd)


def sha(repo, ref):
    return subprocess.run(
        ["git", "rev-parse", ref],
        capture_output=True,
        check=True,
        cwd=repo,
        text=True,
    ).stdout.strip()


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
        DROPPED,
        MERGED,
        MINE,
        OPENED,
        PUSHED,
        QUEUED,
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
    git(repo, "checkout", "--quiet", DROPPED)
    git(repo, "-c", "user.name=t", "commit", "--allow-empty", "-qm", "fw conflict fix")
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
        "busy": {QUEUED},
        "closed": [],
        "running": {str(root / "master" / CLEAN)},
        "stopped": [],
        "tabs": [],
        "vscode": {CLEAN},
        "windows": {REPORTED, TYPED},
        "state": {"queue": [{"bundle": REPORTED, "parent": CLEAN}]},
    }
    for folder in ("bundle-db", "extensions"):
        (root / folder / LEFT).mkdir(parents=True)
    daemon.BUNDLE_DB_CONTAINER = str(root / "bundle-db")
    daemon.CLAUDE_CONFIG_CONTAINER = str(root / "claude-config")
    daemon.EXTENSIONS_CONTAINER = str(root / "extensions")
    daemon.HELD_DIR = root / "held"
    daemon.STATE = root
    daemon.close_window = world["closed"].append
    daemon.get_bundle_container = lambda bundle: (
        bundle if str(root / "master" / bundle) in world["running"] else None
    )
    daemon.get_transcript = lambda session: root / "transcripts" / f"{session}.jsonl"
    daemon.get_window_pids = lambda bundle: [1] if bundle in world["windows"] else []
    daemon.has_window = lambda container: container in world["vscode"]
    daemon.is_in_turn = lambda bundle: bundle in world["busy"]
    daemon.stop_container = lambda container: (
        world["stopped"].append(container) or world["running"].discard(str(root / "master" / container))
    )
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
    daemon.launch_queued = lambda: None
    daemon.is_in_container = lambda connection: world["caller"] != daemon.HOST
    daemon.read_state = lambda: world["state"]
    daemon.run_script = lambda log, script, *args: world["calls"].append((script, *args))
    daemon.write_state = lambda state: None
    daemon.tab_groups.ask = lambda request: world["tabs"].append(request) or {
        "groups": [{"bundle": REPORTED, "title": f"🚀 {REPORTED}"}],
    }
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
            == (0, [f"waits for Seb: hok b1. {waits(CLEAN)}"]),
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
            == (0, [f"waits for Seb: hok b2. {waits(CHILD)}"]),
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
            "a yes asks the VS Code window of an idle bundle to close, and waits for its stop",
            (
                result["code"],
                result["text"],
                world["calls"],
                world["state"]["deletions"],
                (root / "master" / CLEAN / ".agent" / "close-window").exists(),
            )
            == (0, f"{CLEAN} {PENDING}", [], [CLEAN], True),
        ),
    )
    world["running"].clear()
    world["state"]["builds"] = {CLEAN: ["create_bundle.py", CLEAN]}
    daemon.delete_stopped()
    wait_jobs()
    checks.append(
        (
            "the stop of the container deletes it, and its build that never ran",
            (world["calls"], world["state"]["deletions"], world["state"]["builds"])
            == ([("delete_bundle.py", CLEAN)], [], {}),
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
    code, lines = run_client(path, "delete", "master-gone--seb", "--reason", "r")
    checks.append(
        (
            "a bundle with no folder nor branch left is refused",
            (code, lines) == (1, ["bctl: 400 no bundle master-gone--seb"]),
        ),
    )
    code, lines = run_client(path, "delete", LEFT, "--reason", "its deletion failed")
    number = int(lines[0].partition("hok b")[2].partition(".")[0])
    item = daemon.HELD.read_json(daemon.HELD_DIR / f"{number}.json")
    result = answer_yes(number)
    checks.append(
        (
            "the folders a failed deletion left still go, and hok names them",
            (item["lines"][:3], result["text"], world["calls"][-1])
            == (
                [
                    f"the folder {root / 'bundle-db' / LEFT}",
                    f"the folder {root / 'extensions' / LEFT}",
                    "no branch",
                ],
                f"{LEFT} deleted",
                ("delete_bundle.py", LEFT),
            ),
        ),
    )
    world["running"].update(str(root / "master" / bundle) for bundle in (REPORTED, TYPED))
    code, lines = run_client(path, "delete", REPORTED, "--reason", "r")
    number = int(lines[0].partition("hok b")[2].partition(".")[0])
    item = daemon.HELD.read_json(daemon.HELD_DIR / f"{number}.json")
    checks.append(
        (
            "the request shows the report its session ended on",
            (lines, item["lines"][3:], item["yes"])
            == (
                [f"waits for Seb: hok b{number}. {waits(REPORTED)}"],
                [
                    f"its queued task, from {CLEAN}",
                    f"its tab groups in Firefox: 🚀 {REPORTED}",
                    "its agent window, which ends on this report:",
                    "  blocked: needs opus",
                    "",
                    "  - measured",
                ],
                "once its agent ended its turn, its window closes and its container stops, then"
                " delete_bundle deletes it",
            ),
        ),
    )
    world["tabs"].clear()
    result = answer_yes(number)
    kept = root / "deleted" / f"{REPORTED}.md"
    checks.append(
        (
            "the yes closes the window of an idle agent, names where its answer is kept, and drops"
            " the queued task",
            (result["text"], world["closed"], world["state"]["queue"], world["state"]["deletions"])
            == (
                f"{REPORTED} {PENDING}, its last answer is kept in {kept}",
                [REPORTED],
                [],
                [REPORTED],
            ),
        ),
    )
    checks.append(
        (
            "the yes closes its tab groups in Firefox",
            world["tabs"] == [{"op": "close", "bundle": REPORTED}],
        ),
    )
    world["running"].discard(str(root / "master" / REPORTED))
    daemon.delete_stopped()
    wait_jobs()
    checks.append(
        (
            "the stop of its container deletes it, and its report stays on the host",
            (world["calls"][-1], kept.read_text()) == (("delete_bundle.py", REPORTED), f"{REPORT}\n"),
        ),
    )
    world["busy"].add(TYPED)
    code, lines = run_client(path, "delete", TYPED, "--reason", "r")
    number = int(lines[0].partition("hok b")[2].partition(".")[0])
    item = daemon.HELD.read_json(daemon.HELD_DIR / f"{number}.json")
    answer_yes(number)
    checks.append(
        (
            "a session that went on after its report is not shown, and its turn keeps its window",
            (len(item["lines"]), world["closed"]) == (3, [REPORTED]),
        ),
    )
    world["busy"].discard(TYPED)
    daemon.close_deleted()
    checks.append(("its window closes once its turn ended", world["closed"] == [REPORTED, TYPED]))
    world["windows"].discard(TYPED)
    daemon.delete_safely()
    wait_jobs()
    checks.append(
        (
            "a container left with no window is stopped, and its last answer kept",
            (world["stopped"], world["calls"][-1], (root / "deleted" / f"{TYPED}.md").read_text())
            == ([TYPED], ("delete_bundle.py", TYPED), "because\n"),
        ),
    )
    first = sha(root / "odoo", DROPPED)
    code, lines = run_client(path, "delete", DROPPED, "--reason", "r", "--drop", first[:6])
    checks.append(
        (
            "a sha shorter than 7 characters is no commit to drop",
            (code, lines)
            == (1, [f"bctl: 400 not a commit sha of 7 characters or more: {first[:6]}"]),
        ),
    )
    base = sha(root / "odoo", "master")
    code, lines = run_client(path, "delete", DROPPED, "--reason", "r", "--drop", base)
    checks.append(
        (
            "a commit a remote has is no commit to drop",
            (code, lines) == (1, [f"bctl: 400 no commit of {DROPPED} that no remote has: {base}"]),
        ),
    )
    world["running"].add(str(root / "master" / DROPPED))
    code, lines = run_client(path, "delete", DROPPED, "--reason", "r", "--drop", first[:12])
    number = int(lines[0].partition("hok b")[2].partition(".")[0])
    item = daemon.HELD.read_json(daemon.HELD_DIR / f"{number}.json")
    checks.append(
        (
            "a commit of Seb no remote has goes once named, and hok lists it",
            (item["lines"][1:3], item["send"])
            == (
                [
                    "the branch in odoo: never pushed, commits of t@t that no remote has",
                    f"its commit in odoo that no remote has, lost: {first[:12]} fw conflict fix",
                ],
                {"bundle": DROPPED, "drop": [first]},
            ),
        ),
    )
    git(root / "odoo", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "after the request")
    result = answer_yes(number)
    checks.append(
        (
            "a commit not named keeps the bundle",
            (result["code"], result["text"])
            == (1, f"{DROPPED} has commits in odoo that no remote has"),
        ),
    )
    second = sha(root / "odoo", DROPPED)
    code, lines = run_client(path, "delete", DROPPED, "--reason", "r", "--drop", first, "--drop", second)
    calls = len(world["calls"])
    answer_yes(int(lines[0].partition("hok b")[2].partition(".")[0]))
    world["running"].discard(str(root / "master" / DROPPED))
    daemon.delete_stopped()
    wait_jobs()
    checks.append(
        (
            "the commits named still go at the stop of the container, with --force",
            (world["calls"][calls:], world["state"]["drops"])
            == ([("delete_bundle.py", "--force", DROPPED)], {}),
        ),
    )
    world["running"].add(str(root / "master" / QUEUED))
    code, lines = run_client(path, "delete", QUEUED, "--reason", "moved to 17.0")
    answer_yes(int(lines[0].partition("hok b")[2].partition(".")[0]))
    daemon.enqueue(CLEAN, QUEUED, {"task": "port the fix to 19.0"})
    calls = len(world["calls"])
    world["running"].discard(str(root / "master" / QUEUED))
    daemon.delete_stopped()
    wait_jobs()
    checks.append(
        (
            "a task queued after the yes keeps the bundle at the stop of its container",
            (world["calls"][calls:], QUEUED in world["state"]["deletions"]) == ([], False),
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
    week = now - 8 * 86400
    calls, world = [], {"building": [], "closed": {}, "open": set(), "queue": []}
    bundles = (
        "master-building-aku",
        "master-closed-aku",
        "master-commit-aku",
        "master-fixed-aku",
        "master-grouped-aku",
        "master-held-aku",
        "master-idle--seb",
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
    git(repo, "checkout", "--quiet", "-b", "master-idle--seb", "master")
    commit_as_aku(repo, "picked from aku")
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
            os.utime(path, (week, week) if bundle == "master-idle--seb" else (old, old))
    os.utime(root / "master" / "master-recent-aku" / daemon.MADE, (recent, recent))
    os.utime(root / "wt" / "master-commit-aku" / "logs" / "HEAD", (recent, recent))
    os.utime(root / "claude-config" / "master-tab-aku" / ".claude.json", (recent, recent))
    for bundle, stop in (
        ("master-building-aku", closed),
        ("master-closed-aku", closed),
        ("master-mine--seb", closed),
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
    daemon.HOK_WAITING = root / "odoo" / ".git" / "HEAD"
    daemon.read_hok_waiting = lambda: [{"caller": "master-held-aku"}]
    daemon.tab_groups.ask = lambda request: {"groups": [{"bundle": "master-grouped-aku"}]}
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
            "nor one with a hok request waiting or a Firefox tab group open",
            not {("master-grouped-aku",), ("master-held-aku",)} & set(calls),
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
            ("master-closed-aku",) in calls and len(calls) == 5,
        ),
    )
    checks.append(
        (
            "a bundle of Seb goes after 7 days, never with --force, not 10 minutes after its stop",
            ("master-idle--seb",) in calls and not {("master-mine--seb",)} & set(calls),
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


def check_parked(root):
    """A review bundle whose session waits paused in the queue loses its checkout once its
    container stopped, and its task gets the script that fetches it again; a bare one stays, as
    does a bare one whose session waits for disk space to build it, and a refused deletion leaves
    the task as it was."""
    closed = time.time() - 20 * 60
    calls, queue, world_closed = [], [], {}
    bundles = ("master-bare-aku", "master-disk-aku", "master-parked-aku", "master-refused-aku")
    for bundle in bundles:
        folder = root / "master" / bundle
        (folder / ".agent").mkdir(parents=True)
        (folder / ".agent" / "session").write_text(f"{bundle}-session\n")
        paths = [folder / ".agent" / "session"]
        if bundle not in ("master-bare-aku", "master-disk-aku"):
            paths.append(folder / daemon.MADE)
            paths[-1].touch()
        for path in paths:
            os.utime(path, (closed - 60, closed - 60))
        world_closed[str(folder)] = closed
        queue.append({"bundle": bundle, "paused": True, "resume": True, "task": daemon.GO_ON})
    build = ["fetch_bundle.py", "master-disk-aku", "--no-open"]
    queue[1].update(build=build, paused=False, session="master-disk-aku-session")

    def run_script(log, script, *args):
        calls.append(args[-1])
        if args[-1] == "master-refused-aku":
            raise daemon.Refused("delete_bundle.py exited with 1")
        shutil.rmtree(root / "master" / args[-1])

    daemon.CLAUDE_CONFIG_CONTAINER = str(root / "claude-config")
    daemon.get_agent_folder = lambda bundle: root / "master" / bundle / ".agent"
    daemon.get_closed_windows = lambda: dict(world_closed)
    daemon.get_open_windows = set
    daemon.get_repos = list
    daemon.get_worktree_bundle_folder = lambda bundle: str(root / "master" / bundle)
    daemon.get_worktree_container_folder = lambda: str(root)
    daemon.local_branches = lambda bundle: []
    daemon.read_state = lambda: {"building": [], "queue": queue}
    daemon.run_script = run_script
    daemon.write_state = lambda state: None
    daemon.drop_idle_reviews()
    wait_jobs()
    items = {item["bundle"]: item for item in queue}
    checks = [
        ("a made bundle parked in the queue goes, not a bare one", sorted(calls) == list(bundles[2:])),
        (
            "its task keeps the session and fetches the bundle again",
            (items["master-parked-aku"].get("build"), items["master-parked-aku"].get("session"))
            == (["fetch_bundle.py", "master-parked-aku", "--no-open"], "master-parked-aku-session"),
        ),
        ("a refused deletion leaves the task to its bundle", "build" not in items["master-refused-aku"]),
        ("the bare one keeps its folder", (root / "master" / "master-bare-aku" / ".agent").is_dir()),
        (
            "the bare one that waits for disk space keeps its folder and its build",
            (
                (root / "master" / "master-disk-aku" / ".agent").is_dir(),
                items["master-disk-aku"].get("build"),
            )
            == (True, build),
        ),
    ]
    return [name for name, passed in checks if not passed], len(checks)


def check_gone(root):
    """A bare bundle deleted as unused keeps the session of its agent, and the Agent button of its
    Firefox group (`bctl open --terminal` from the host) goes on with it in its bare folder made
    again, which `bctl build` fetches."""
    bundle, session = "master-19.0-fix--seb-1-fw", "0f897331-3dea-4b5b-a0c4-1c27061e4efc"
    folder = root / "master" / bundle
    (folder / ".agent").mkdir(parents=True)
    (folder / ".agent" / "session").write_text(f"{session}\n")
    old = time.time() - 30 * 60
    os.utime(folder / ".agent" / "session", (old, old))
    transcript = root / f"{session}.jsonl"
    answer = {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}
    transcript.write_text(f"{json.dumps(answer)}\n")
    state, opened, calls = {"building": [], "builds": {}, "queue": []}, [], []

    def run_script(log, script, *args):
        calls.append((script, *args))
        shutil.rmtree(folder)

    daemon.CLAUDE_CONFIG_CONTAINER = str(root / "claude-config")
    daemon.HOK_WAITING = root / "hok-waiting"
    daemon.STATE = root
    daemon.get_agent_folder = lambda name: root / "master" / name / ".agent"
    daemon.get_closed_windows = lambda: {str(folder): old}
    daemon.get_open_windows = set
    daemon.get_transcript = lambda name: transcript if name == session else None
    daemon.get_worktree_bundle_folder = lambda name: str(root / "master" / name)
    daemon.get_worktree_container_folder = lambda: str(root)
    daemon.local_branches = lambda name: []
    daemon.open_terminal = lambda log, name: opened.append(name)
    daemon.prepare_folder = lambda name: (root / "master" / name / ".claude").mkdir(parents=True)
    daemon.read_state = lambda: state
    daemon.run_script = run_script
    daemon.tab_groups.ask = lambda request: None
    daemon.write_state = lambda new: None
    daemon.drop_idle_reviews()
    wait_jobs()
    checks = [
        (
            "a bare bundle unused goes, and keeps its session",
            (folder.exists(), daemon.read_text(root / "deleted" / f"{bundle}.session"))
            == (False, session),
        ),
        (
            "with delete_bundle.py, which removes its container and its stores",
            calls == [("delete_bundle.py", bundle)],
        ),
    ]

    def open_terminal(caller, name=bundle):
        try:
            code, _answer = daemon.open_bundle(caller, {}, {"bundle": name, "terminal": True})
        except (ValueError, daemon.Refused) as error:
            return str(error)
        wait_jobs()
        return code

    checks += [
        ("a bundle cannot open it", open_terminal("other") == f"no bundle folder for {bundle}"),
        (
            "nor the host one with no kept session",
            open_terminal(daemon.HOST, "master-x--seb") == "no bundle folder for master-x--seb",
        ),
        (
            "the host opens its agent on its session, bare, to build by fetching it",
            (
                open_terminal(daemon.HOST),
                opened,
                daemon.read_text(folder / ".agent" / "session"),
                daemon.read_text(folder / ".agent" / "task.md"),
                daemon.is_bare(folder),
                state["builds"],
            )
            == (
                202,
                [bundle],
                session,
                "",
                True,
                {bundle: ["fetch_bundle.py", bundle, "--no-open"]},
            ),
        ),
    ]
    shutil.rmtree(folder, ignore_errors=True)
    state["queue"].append({"bundle": bundle})
    checks.append(
        (
            "not while its task waits in the queue",
            open_terminal(daemon.HOST)
            == f"{bundle}: its task waits in the queue, `bctl schedule {bundle} spot`",
        ),
    )
    return [name for name, passed in checks if not passed], len(checks)


def check_pairs(root):
    """The bug and fix trees of serve-for-seb go 10 minutes after the container of their bundle
    stopped, with the worktree entry of an older pair, and nothing else of the bundle."""
    now = time.time()
    closed, recent, old = "master-closed--seb", "master-recent--seb", "master-old--seb"
    worktrees = root / "odoo" / ".git" / "worktrees"
    for bundle in (closed, recent, old):
        folder = root / "master" / bundle
        for path in (
            ".bug-fix",
            "bug-fix-shots",
            "odoo",
            f"{bundle}-bug-odoo",
            f"{bundle}-fix-odoo",
        ):
            (folder / path).mkdir(parents=True)
    for name, gitdir, head in (
        (f"{old}-bug-odoo", f"/workspace/{old}-bug-odoo/.git", "a" * 40),
        (f"{old}-fix-odoo", f"/workspace/{old}-fix-odoo/.git", "ref: refs/heads/other"),
        ("odoo3", "/workspace/odoo/.git", f"ref: refs/heads/{old}"),
    ):
        (worktrees / name).mkdir(parents=True)
        (worktrees / name / "gitdir").write_text(f"{gitdir}\n")
        (worktrees / name / "HEAD").write_text(f"{head}\n")
    daemon.get_closed_windows = lambda: {
        str(root / "master" / closed): now - 20 * 60,
        str(root / "master" / old): now - 20 * 60,
        str(root / "master" / recent): now - 5 * 60,
    }
    daemon.get_open_windows = set
    daemon.get_repo_folder = lambda repo_name: str(root / "odoo")
    daemon.get_repos = lambda: ["odoo"]
    daemon.drop_closed_pairs()

    def left(bundle):
        return sorted(path.name for path in (root / "master" / bundle).iterdir())

    checks = [
        (
            "a pair goes 10 minutes after its container stopped, its shots and the bundle stay",
            (left(closed), left(old)) == (["bug-fix-shots", "odoo"], ["bug-fix-shots", "odoo"]),
        ),
        (
            "with the worktree entry of an older pair, not one on a branch",
            sorted(path.name for path in worktrees.iterdir()) == [f"{old}-fix-odoo", "odoo3"],
        ),
        ("a pair stopped 5 minutes ago stays", len(left(recent)) == 5),
    ]
    return [name for name, passed in checks if not passed], len(checks)


def check_close(root):
    """`bctl close` from the host closes an idle agent window, and from a bundle gets a 403; a turn
    that runs, a dialog, an agent that starts or one on a worker keeps the window."""
    idle, busy, starting, remote, stuck, bare = (
        f"master-{name}--seb" for name in ("idle", "busy", "starting", "remote", "stuck", "bare")
    )
    for bundle in (idle, busy, starting, remote, stuck, bare):
        (root / "master" / bundle / ".agent").mkdir(parents=True)
        (root / "master" / bundle / ".agent" / "session").write_text(f"{bundle}-session\n")
    world = {
        "caller": idle,
        "closed": [],
        "windows": {idle, busy, starting, remote, stuck},
    }

    def close_freed(bundle):
        world["closed"].append(bundle)
        if bundle != stuck:
            world["windows"].discard(bundle)

    daemon.close_freed = close_freed
    daemon.cut.get_live_sessions = lambda folders: {f"{bundle}-session" for bundle in (idle, busy)}
    daemon.get_agent_folder = lambda bundle: root / "master" / bundle / ".agent"
    daemon.get_window_pids = lambda bundle: [1] if bundle in world["windows"] else []
    daemon.get_worktree_bundle_folder = lambda bundle: str(root / "master" / bundle)
    daemon.identify = lambda connection: world["caller"]
    daemon.is_agent_running = lambda bundle: bundle in (idle, busy, starting)
    daemon.is_in_container = lambda connection: world["caller"] != daemon.HOST
    daemon.is_in_turn = lambda bundle: bundle in (busy, remote)
    daemon.remote_agent.is_remote = lambda folder: folder.name == remote
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    code, lines = run_client(path, "close", idle)
    checks.append(
        (
            "a bundle cannot close a window, its own neither",
            (code, lines, world["closed"])
            == (1, ["bctl: 403 only from the host, a container cannot ask it"], []),
        ),
    )
    world["caller"] = daemon.HOST
    for name, bundle, error in (
        (
            "a turn that runs keeps the window",
            busy,
            "its claude is in a turn or waits for an answer",
        ),
        (
            "an agent that starts keeps its window",
            starting,
            "its agent is starting, its claude does not run yet",
        ),
        (
            "an agent on a worker keeps its window",
            remote,
            "its agent runs on a worker, this host cannot see its turn",
        ),
    ):
        code, lines = run_client(path, "close", bundle)
        checks.append(
            (name, (code, lines, world["closed"]) == (1, [f"bctl: 409 {bundle}: {error}"], [])),
        )
    code, lines = run_client(path, "close", bare)
    checks.append(
        (
            "a bundle with no agent window says so",
            (code, lines) == (1, [f"bctl: 400 {bare} has no agent window"]),
        ),
    )
    code, lines = run_client(path, "close", "master-gone--seb")
    checks.append(
        (
            "a bundle with no folder says so",
            (code, lines) == (1, ["bctl: 400 no bundle folder for master-gone--seb"]),
        ),
    )
    code, lines = run_client(path, "close", idle)
    checks.append(
        (
            "the host closes the window of an idle claude",
            (code, lines, world["closed"])
            == (
                0,
                [
                    f"{idle}: its agent window is closed, bctl open {idle} --terminal goes on with its session"
                ],
                [idle],
            ),
        ),
    )
    code, lines = run_client(path, "close", stuck)
    checks.append(
        (
            "a window still open after the close says so",
            (code, lines) == (1, [f"bctl: 409 {stuck}: its agent window is still open 30 s later"]),
        ),
    )
    server.shutdown()
    server.server_close()
    return [name for name, passed in checks if not passed], len(checks)


def check_git_config(root):
    """The git of the daemon runs none of the programs a bundle can name in the .git/config of a
    repo: here a core.fsmonitor and a hook that leave a mark, on the status before a deletion."""
    repo, mark = root / "odoo", root / "mark"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "file").write_text("x\n")
    git(repo, "add", "file")
    program = root / "program"
    program.write_text(f'#!/bin/sh\necho "$0 $*" >>{mark}\n')
    program.chmod(0o755)
    git(repo, "config", "core.fsmonitor", str(program))
    (repo / ".git" / "hooks" / "post-index-change").symlink_to(program)
    (repo / "file").touch()
    daemon.get_repos = lambda: ["odoo"]
    daemon.get_worktree_bundle_repo_folder = lambda bundle, name: str(root / name)
    try:
        daemon.check_uncommitted("b")
    except daemon.Refused:
        pass
    if mark.exists():
        return [f"the status ran {mark.read_text()!r}"], 1
    return [], 1


def main():
    with tempfile.TemporaryDirectory() as root:
        failures, total = check_git_config(pathlib.Path(root))
    with tempfile.TemporaryDirectory() as root:
        more, count = check(pathlib.Path(root))
        failures, total = failures + more, total + count
    with tempfile.TemporaryDirectory() as root:
        more, count = check_reviews(pathlib.Path(root))
        failures, total = failures + more, total + count
    with tempfile.TemporaryDirectory() as root:
        more, count = check_parked(pathlib.Path(root))
        failures, total = failures + more, total + count
    with tempfile.TemporaryDirectory() as root:
        more, count = check_gone(pathlib.Path(root))
        failures, total = failures + more, total + count
    with tempfile.TemporaryDirectory() as root:
        more, count = check_pairs(pathlib.Path(root))
        failures, total = failures + more, total + count
    with tempfile.TemporaryDirectory() as root:
        more, count = check_close(pathlib.Path(root))
        failures, total = failures + more, total + count
    for name in failures:
        print(f"failed: {name}")
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
