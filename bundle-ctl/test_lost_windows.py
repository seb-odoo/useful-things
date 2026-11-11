#!/usr/bin/env python3
"""The agent windows a shutdown or a crash closed: the note each window keeps and drops when
someone closes it, which notes the daemon takes for a lost window, which agents it parks in the
queue, the one card it holds for Seb, and what his yes resumes: python3 test_lost_windows.py
"""

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon
from test_cut import ASKED, STARTED, answer, result, session, title, write

agents = sys.modules["agents"]

CUT = "master-cut--seb"
WAITING = "master-waiting--seb"
DONE = "master-done--seb"
WATCH = "master-watch--seb"
REMOTE = "master-remote--seb"
GONE = "master-gone--seb"
QUEUED = "master-queued--seb"
STOPPING = "master-stopping--seb"
VSCODE = "master-vscode--seb"
LATER = "master-later--seb"
OPS_WAIT = "ops-00000000-0000-0000-0000-00000000000a"
OPS_DONE = "ops-00000000-0000-0000-0000-00000000000b"
OPS_LIVE = "ops-00000000-0000-0000-0000-00000000000c"
BOOT = "boot-1"


def check(checks, prefix):
    return [f"{prefix}: {name}" for name, passed in checks if not passed], len(checks)


def check_notes(root):
    """A window writes its note; a close of another window of the same name keeps it, its own
    close drops it. A window closed by SIGUSR1 or that ends by itself drops it, not one that X or a
    shutdown ended."""
    path = agents.WINDOWS / f"{CUT}.json"
    agents.write_window_note(CUT, 10)
    written = json.loads(path.read_text())
    agents.drop_window_note(CUT, 11)
    kept = path.exists()
    agents.drop_window_note(CUT, 10)
    spec = importlib.util.spec_from_file_location("terminator", HERE / "agent-terminator.py")
    terminator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(terminator)
    stopping = []
    terminator.is_host_stopping = lambda: bool(stopping)
    on_purpose = [terminator.is_closed_on_purpose(code) for code in (0, 1, -15)]
    stopping.append(True)
    on_purpose.append(terminator.is_closed_on_purpose(0))
    terminator.closing.append(True)
    on_purpose.append(terminator.is_closed_on_purpose(-15))
    return check(
        [
            ("the note names its process and boot", written == {"boot": BOOT, "pid": 10}),
            ("the close of another window keeps it", kept),
            ("its own close drops it", not path.exists()),
            ("closed by itself, by X, by a kill, at a shutdown, by SIGUSR1", on_purpose == [
                True, False, False, False, True,
            ]),
        ],
        "notes",
    )


def note(name, pid, age=0, **extra):
    path = agents.WINDOWS / f"{name}.json"
    path.write_text(json.dumps({"boot": BOOT, "pid": pid, **extra}))
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def check_find(root):
    """A note whose window lives is touched; one whose window is gone is lost, unless the daemon
    adopted it in this boot, which it drops once gone LOST_AFTER; another window of the name, or a
    window with no note, gets an adopted one; a broken note goes."""
    processes = {"master-live--seb": 100, "master-other--seb": 300, "master-fresh--seb": 400}
    daemon.get_window_processes = lambda: dict(processes)
    live = note("master-live--seb", 100, 500)
    note("master-dead--seb", 200, 30)
    note("master-other--seb", 299)
    note("master-adopted--seb", 500, 10, adopted=True)
    note("master-adopted-old--seb", 501, daemon.LOST_AFTER + 10, adopted=True)
    note("master-adopted-boot--seb", 502, 50, adopted=True, boot="boot-0")
    note("Not A Bundle", 503)
    (agents.WINDOWS / "master-broken--seb.json").write_text("[1]")
    lost = daemon.find_lost_windows()
    left = {path.stem: json.loads(path.read_text()) for path in agents.WINDOWS.glob("*.json")}
    names = sorted(name for name, _path, _seen in lost)
    seen = {name: when for name, _path, when in lost}
    touched = time.time() - live.stat().st_mtime < 2
    for path in agents.WINDOWS.glob("*.json"):
        path.unlink()
    return check(
        [
            ("the lost ones", names == ["master-adopted-boot--seb", "master-dead--seb"]),
            ("when each was last seen", abs(seen["master-dead--seb"] - (time.time() - 30)) < 2),
            ("a live window is touched", touched),
            ("the notes left, the lost ones until parked", sorted(left) == [
                "master-adopted--seb",
                "master-adopted-boot--seb",
                "master-dead--seb",
                "master-fresh--seb",
                "master-live--seb",
                "master-other--seb",
            ]),
            ("another window of the name is adopted", left["master-other--seb"] == {
                "adopted": True, "boot": BOOT, "pid": 300,
            }),
            ("a window with no note is adopted", left["master-fresh--seb"]["adopted"]),
        ],
        "find",
    )


def prepare(root):
    """Bundles and ops sessions whose window a shutdown closed, each with its transcript."""
    src, own, projects = root / "src", root / "transcripts", root / "projects"
    agents.TRANSCRIPTS = daemon.TRANSCRIPTS = own
    agents.PROJECTS = projects
    asked, started = ASKED[1:], STARTED[1:]
    bundles = {
        CUT: asked,
        WAITING: [*asked, result(), answer("The tests pass.\n\nWaiting for you: hok g12")],
        DONE: [*asked, result(), answer("needs Seb: review it\n\nWaiting for you")],
        WATCH: [*started, answer("needs Seb: submit your review.\n\nWaiting for you: hok r3")],
        REMOTE: asked,
        QUEUED: asked,
        STOPPING: asked,
        VSCODE: asked,
        LATER: [*asked, result(), answer("Nothing yet.")],
    }
    for number, (bundle, entries) in enumerate(bundles.items()):
        (src / bundle / ".agent").mkdir(parents=True)
        (src / bundle / ".agent" / "session").write_text(f"{session('a', number)}\n")
        write(own / bundle / f"{session('a', number)}.jsonl", [title(bundle), *entries], 60)
    (src / CUT / ".agent" / "priority").write_text("5\n")
    (src / CUT / ".agent" / "model").write_text("sonnet\n")
    ops = {
        OPS_WAIT: [*ASKED[1:], result(), answer("**Done**\n- it\n\nWaiting for you: hok 12")],
        OPS_DONE: [*ASKED[1:], result(), answer("**Done**\n- it\n\nNothing left: it is pushed.")],
        OPS_LIVE: ASKED[1:],
    }
    for name, entries in ops.items():
        entries = [{"type": "ai-title", "aiTitle": f"Ops {name[-1]}"}, *entries]
        write(agents.get_projects(name)[0] / f"{name.removeprefix(daemon.OPS)}.jsonl", entries, 60)
    state = {"launches": {}, "queue": [{"bundle": QUEUED, "paused": False, "queued": 1.0}]}
    daemon.get_worktree_bundle_folder = lambda name: str(src / name)
    daemon.get_agent_folder = lambda name: (
        root / "ops" / name if name.startswith(daemon.OPS) else src / name / ".agent"
    )
    daemon.read_state = lambda: state
    daemon.write_state = lambda new: state.update(new)
    daemon.launch_queued = lambda: False
    daemon.get_bundle_container = lambda bundle: bundle if bundle in (STOPPING, VSCODE) else None
    daemon.has_window = lambda container: container == VSCODE
    daemon.get_window_pids = lambda name: []
    daemon.get_open_windows = set
    daemon.get_live = lambda: {OPS_LIVE.removeprefix(daemon.OPS)}
    daemon.is_deleting = lambda name: False
    daemon.remote_agent.is_remote = lambda folder: folder.name == REMOTE
    daemon.HELD_DIR = root / "held"
    daemon.get_window_processes = dict
    return state


def check_park(root, state):
    """A cut turn, a turn that ended with no verdict, a background command still running and an
    ops session that did not end on Nothing left are parked, paused, with the priority and the
    model of their last task; a verdict, Nothing left, an agent on a worker, a folder gone, a task
    queued already, a VS Code window that holds the session, and an ops session a claude still
    runs past LOST_WAIT are left; a container that still stops is asked again at the next pass.
    One card lists them."""
    names = (
        CUT, WAITING, DONE, WATCH, REMOTE, GONE, QUEUED, STOPPING, VSCODE, OPS_WAIT, OPS_DONE,
    )
    for pid, name in enumerate(names, 1000):
        note(name, pid, 60)
    note(OPS_LIVE, 999, daemon.LOST_WAIT + 10)
    daemon.restore_lost_windows()
    waits = sorted(path.stem for path in agents.WINDOWS.glob("*.json"))
    (agents.WINDOWS / f"{STOPPING}.json").unlink()
    parked = {item["bundle"]: item for item in state["queue"] if item.get("restored")}
    held = json.loads((daemon.HELD_DIR / "1.json").read_text())
    task = parked[CUT]["task"] if CUT in parked else ""
    return check(
        [
            ("the parked ones", sorted(parked) == sorted([CUT, WAITING, WATCH, OPS_WAIT])),
            ("each paused, as a resume of its session", all(
                item["paused"] and item["resume"] and item["session"] for item in parked.values()
            )),
            ("the priority and the model of its last task", (
                parked[CUT]["priority"], parked[CUT]["model"], parked[OPS_WAIT]["priority"],
            ) == (5, "sonnet", 0)),
            ("why each had work left", [parked[name]["restored"]["why"] for name in (
                CUT, WAITING, WATCH, OPS_WAIT,
            )] == [
                "a tool call ran",
                "its last answer gave no verdict",
                "a background command or agent it started still ran",
                "its last answer did not end on Nothing left",
            ]),
            ("a cut turn is told its last call", task.endswith("Your last call: Bash git push")),
            ("an ended turn is told none", "Your last call" not in parked[WAITING]["task"]),
            ("it says why it stopped", daemon.LOST_WHY in task),
            ("the note of a container that still stops waits", waits == [STOPPING]),
            ("one card", sorted(path.name for path in daemon.HELD_DIR.glob("*.json")) == [
                "1.json",
            ]),
            ("its title counts them", held["title"] == (
                "resume the 4 agents a shutdown or a crash closed"
            )),
            ("it names each", [line for line in held["lines"] if line and line[0] != " "][:4] == [
                CUT, WAITING, WATCH, f"{OPS_WAIT}: Ops a",
            ]),
            ("it sends each", [name for name, _queued in held["send"]["restore"]] == [
                CUT, WAITING, WATCH, OPS_WAIT,
            ]),
        ],
        "park",
    )


def check_again(root, state):
    """A window lost before Seb's answer asks the card again with it, under the same number."""
    note(LATER, 2000, 60)
    daemon.restore_lost_windows()
    held = json.loads((daemon.HELD_DIR / "1.json").read_text())
    return check(
        [
            ("still one card", not (daemon.HELD_DIR / "2.json").exists()),
            ("it holds the new one", held["title"] == (
                "resume the 5 agents a shutdown or a crash closed"
            ) and held["send"]["restore"][-1][0] == LATER),
        ],
        "again",
    )


def check_yes(root, state):
    """Seb's yes takes each one still paused out of the pause; one resumed by hand since is told."""
    path = daemon.HELD_DIR / "1.json"
    item = daemon.HELD.read_json(path)
    by_hand = next(entry for entry in state["queue"] if entry["bundle"] == WATCH)
    by_hand["paused"] = False
    path.with_suffix(".answer").write_text(f"yes {item['digest']}\n")
    daemon.HELD.take(path.with_suffix(".answer"), daemon.send_held)
    text = daemon.HELD.read_json(path.with_suffix(".result"))["text"]
    paused = [entry["bundle"] for entry in state["queue"] if entry.get("paused")]
    return check(
        [
            ("none waits paused", paused == []),
            ("hok says what ran", text == (
                f"out of the pause, started as the queue lets them: {CUT}, {WAITING}, {OPS_WAIT},"
                f" {LATER}; no longer paused in the queue: {WATCH}"
            )),
        ],
        "yes",
    )


def main():
    failures, total = [], 0
    with tempfile.TemporaryDirectory() as folder:
        root = pathlib.Path(folder)
        agents.WINDOWS = root / "windows"
        agents.WINDOWS.mkdir()
        agents.get_boot = daemon.get_boot = lambda: BOOT
        for step in (check_notes, check_find):
            failed, count = step(root)
            failures += failed
            total += count
        state = prepare(root)
        for step in (check_park, check_again, check_yes):
            failed, count = step(root, state)
            failures += failed
            total += count
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
