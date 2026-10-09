#!/usr/bin/env python3
"""What a pass of the queue launches, keeps and drops, without podman: python3 test_queue.py"""

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import daemon  # noqa: E402

QUEUED = 1760000000.0
TASK = "do"

FRESH = {}
GONE = None
RUNS_IT = {"queued": f"{QUEUED}\n", "session": "s\n", "task.md": TASK, "terminal": "1\n"}
RUNS_ANOTHER = {**RUNS_IT, "queued": f"{QUEUED - 60}\n"}

CASES = [
    ({"fresh": FRESH}, ["fresh"], []),
    ({"gone": GONE, "fresh": FRESH}, ["fresh"], []),
    ({"taken": RUNS_IT, "gone": GONE, "fresh": FRESH}, ["fresh"], []),
    ({"busy": RUNS_ANOTHER, "fresh": FRESH}, ["fresh"], ["busy"]),
]


def run_pass(root, bundles):
    """One pass on a queue of these bundles, each given as its `.agent` files, None when its
    folder is gone: the bundles it launches, and the ones it leaves in the queue.
    """
    folders = {bundle: str(root / "master" / bundle) for bundle in bundles}
    for bundle, files in bundles.items():
        if files is None:
            continue
        agent = pathlib.Path(folders[bundle]) / ".agent"
        agent.parent.mkdir(parents=True)
        if files:
            agent.mkdir()
        for name, text in files.items():
            (agent / name).write_text(text)
    state = {
        "launches": {},
        "queue": [
            {"bundle": bundle, "parent": "host", "queued": QUEUED, "task": TASK}
            for bundle in bundles
        ],
    }
    launched = []
    daemon.get_agent_folder = lambda bundle: pathlib.Path(folders[bundle]) / ".agent"
    daemon.get_bundle_container = lambda bundle: bundle
    daemon.get_open_windows = lambda: {
        folders[bundle] for bundle, files in bundles.items() if files and "terminal" in files
    }
    daemon.get_worktree_bundle_folder = folders.get
    daemon.get_worktree_container_folder = lambda: str(root)
    daemon.has_window = lambda container: False
    daemon.open_terminal = lambda log, bundle: launched.append(bundle)
    daemon.read_state = lambda: state
    daemon.session_env = lambda: {"DISPLAY": ":0"}
    daemon.start_job = lambda caller, verb, work: work(None)
    daemon.write_state = lambda state: None
    daemon.launch_queued()
    return launched, [item["bundle"] for item in state["queue"]]


def main():
    failures = []
    for bundles, launched, queue in CASES:
        with tempfile.TemporaryDirectory() as root:
            try:
                found = run_pass(pathlib.Path(root), bundles)
            except OSError as error:
                found = repr(error)
        if found != (launched, queue):
            failures.append(f"{list(bundles)}: {found}, expected {(launched, queue)}")
    for line in failures:
        print(line)
    print(f"{len(CASES) - len(failures)}/{len(CASES)} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
