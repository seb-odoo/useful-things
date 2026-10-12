#!/usr/bin/env python3
"""What `bctl delete` holds for hok, what it refuses, and when a yes deletes, on git repos of its
own and without podman: python3 test_delete.py
"""

import pathlib
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon  # noqa: E402
from test_queue import run_client  # noqa: E402

CLEAN = "master-clean--seb"
COMMITTED = "master-committed--seb"
PUSHED = "master-pushed--seb"
REFUSED = "master-refused--seb"
STOPPED = "master-stopped--seb"


def git(cwd, *args):
    subprocess.run(["git", *args], capture_output=True, check=True, cwd=cwd)


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
    for bundle in (CLEAN, COMMITTED, PUSHED, REFUSED, STOPPED):
        git(repo, "branch", bundle)
        (root / "master" / bundle).mkdir(parents=True)
    git(repo, "push", "--quiet", "odoo-dev", PUSHED)
    git(repo, "fetch", "--quiet", "--all")
    git(repo, "checkout", "--quiet", COMMITTED)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "own")
    world = {"caller": CLEAN, "calls": [], "running": {str(root / "master" / CLEAN)}, "state": {}}
    daemon.HELD_DIR = root / "held"
    daemon.STATE = root
    daemon.get_open_windows = lambda: set(world["running"])
    daemon.get_repo_folder = lambda repo_name: str(repo)
    daemon.get_repos = lambda: ["odoo"]
    daemon.get_worktree_bundle_folder = lambda bundle: str(root / "master" / bundle)
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
            (code, lines) == (1, ["bctl: 409 a bundle asks for its own deletion only"]),
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
    server.shutdown()
    server.server_close()
    return [name for name, passed in checks if not passed], len(checks)


def main():
    with tempfile.TemporaryDirectory() as root:
        failures, total = check(pathlib.Path(root))
    for name in failures:
        print(f"failed: {name}")
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
