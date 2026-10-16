#!/usr/bin/env python3
"""Which sessions a crash or a restart cut, on fake transcripts and a fake /proc, what `bctl open
--cut --resume` holds for hok, and what Seb's yes resumes: python3 test_cut.py
"""

import json
import os
import pathlib
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cut
import daemon
from daemon import read_text
from test_queue import run_client

BUNDLE = "master-b--seb"
GONE = "master-gone--seb"
MAPPED = "master-d--seb"
NEWER = "master-a--seb"
RUNNING = "master-c--seb"
OPS_CUT = "00000000-0000-0000-0000-000000000001"
OPS_ENDED = "00000000-0000-0000-0000-000000000004"
OPS_LIVE = "00000000-0000-0000-0000-000000000002"
OPS_OLD = "00000000-0000-0000-0000-000000000003"
OPS_TAKEN = "00000000-0000-0000-0000-000000000005"
REPORT = "Done\n\n- **Done**: all of it\n\nNothing left: the fix is pushed."


def message(kind, content, **extra):
    return {"type": kind, "message": {"content": content}, **extra}


def prompt(text):
    return message("user", text, origin={"kind": "human"})


def call(name="Bash", **given):
    return message("assistant", [{"type": "tool_use", "name": name, "input": given}])


def result(**extra):
    return message("user", [{"type": "tool_result", "content": "ok"}], **extra)


def answer(text):
    return message("assistant", [{"type": "text", "text": text}])


def notified(task):
    content = f"<task-notification>\n<task-id>{task}</task-id>\n</task-notification>"
    return {"type": "queue-operation", "operation": "enqueue", "content": content}


def title(name):
    return {"type": "custom-title", "customTitle": name}


def session(letter, number=0):
    return f"0000000{letter}-0000-0000-0000-{number:012}"


ASKED = [title(BUNDLE), prompt("fix it"), call(command="git push")]
STARTED = [*ASKED, result(toolUseResult={"backgroundTaskId": "b1"})]
CASES = [
    ("a tool call with no result", ASKED, "a tool call ran"),
    ("a tool result with no answer", [*ASKED, result()], "a tool result had no answer"),
    (
        "a prompt with no answer",
        [*ASKED, result(), answer("ok"), prompt("go")],
        "a prompt had no answer",
    ),
    ("an answer ends the turn", [*ASKED, result(), answer("pushed")], None),
    (
        "an interruption by Seb",
        [*ASKED, result(), message("user", [{"type": "text", "text": f"{cut.INTERRUPTED}]"}])],
        None,
    ),
    (
        "a /model after the answer",
        [*ASKED, result(), answer("ok"), prompt("<command-name>/model</command-name>")],
        None,
    ),
    (
        "a compaction while idle",
        [*ASKED, result(), answer("ok"), message("user", "summary", isCompactSummary=True)],
        None,
    ),
    (
        "a thinking block after the tool call",
        [*ASKED, message("assistant", [{"type": "thinking", "thinking": "hm"}])],
        "a tool call ran",
    ),
    (
        "a side chain after the answer",
        [*ASKED, result(), answer("ok"), message("user", "go", isSidechain=True)],
        None,
    ),
    (
        "a background command still runs",
        [*STARTED, answer("The tests run in the background.")],
        "it waited for a background command or agent",
    ),
    (
        "a background agent still runs",
        [*ASKED, result(toolUseResult={"isAsync": True, "agentId": "a7"}), answer("It reviews.")],
        "it waited for a background command or agent",
    ),
    ("a background command that ended", [*STARTED, notified("b1"), answer("Waits.")], None),
    (
        "a background command stopped",
        [*STARTED, call("TaskStop", task_id="b1"), result(), answer("Stopped.")],
        None,
    ),
    ("a background command after a report", [*STARTED, answer(REPORT)], None),
    (
        "a background command after a close line",
        [*STARTED, answer("Waiting for you: your review.\n\nSeparately: a note.")],
        None,
    ),
    ("a background command after a verdict", [*STARTED, answer("needs Seb: your review")], None),
    ("a claude -p run", [{**entry, "entrypoint": "sdk-cli"} for entry in ASKED], None),
]


def write(path, entries, age=0, filler=0):
    """A transcript of these entries, `filler` bytes of attachments after its first message, its
    last write `age` seconds ago."""
    lines = [json.dumps(entry) for entry in entries]
    if filler:
        lines[2:2] = [json.dumps({"type": "attachment", "text": "x" * 1000})] * (filler // 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines))
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def check_cases(root):
    failures = []
    for name, entries, expected in CASES:
        session = cut.read_session(write(root / "case.jsonl", entries))
        if cut.get_cut(session) != expected:
            failures.append(f"{name}: {cut.get_cut(session)!r}, expected {expected!r}")
    entries = [*ASKED, result(), answer("ok"), prompt(cut.GO_ON), call(command="pytest")]
    session = cut.read_session(write(root / "case.jsonl", entries))
    found = (session["title"], session["prompt"], session["call"])
    if found != (BUNDLE, "fix it", "Bash pytest"):
        failures.append(f"the title, the last prompt typed and the last call: {found}")
    tail = [*ASKED, {"type": "attachment", "text": "x" * (cut.TAIL + 1000)}]
    session = cut.read_session(write(root / "case.jsonl", tail))
    if cut.get_cut(session) != "a tool call ran":
        failures.append(f"a last line longer than the tail: {cut.get_cut(session)!r}")
    return failures, len(CASES) + 2


def check_live(root):
    """A claude named on its command line, or by a record of its container whose pid and start
    match a claude of the host; a record of a claude gone, or of another program, is not."""
    proc = root / "proc"
    processes = {
        "100": (b"/bin/claude\0--resume\0s-cmdline\0", "100", "7"),
        "200": (b"/x/native-binary/claude\0--output-format\0stream-json\0", "200\t21", "555"),
        "300": (b"python3\0s-other\0", "300\t22", "666"),
    }
    for pid, (cmdline, nspid, start) in processes.items():
        (proc / pid).mkdir(parents=True)
        (proc / pid / "cmdline").write_bytes(cmdline.replace(b"s-cmdline", OPS_CUT.encode()))
        (proc / pid / "status").write_text(f"Name:\tx\nNSpid:\t{nspid}\n")
        (proc / pid / "stat").write_text(f"{pid} (claude a) S {' '.join(['0'] * 18)} {start} 0\n")
    records = root / "config" / "c1" / "sessions"
    records.mkdir(parents=True)
    for name, pid, start in (("tab", 21, "555"), ("gone", 21, "444"), ("other", 22, "666")):
        record = {"pid": pid, "procStart": start, "sessionId": f"s-{name}"}
        (records / f"{name}.json").write_text(json.dumps(record))
    (records / "broken.json").write_text("[1]")
    found = cut.get_live_sessions([records], str(proc))
    expected = {OPS_CUT, "s-tab"}
    return ([] if found == expected else [f"live sessions: {found}, expected {expected}"]), 1


def prepare(root):
    """Transcripts of bundles and of the ops container (projects/-repo), and the bundle folders."""
    projects, src = root / "projects", root / "src" / "master"
    ended = [*ASKED, result(), answer("pushed")]
    bundles = projects / "-workspace"
    write(bundles / f"{session('a', 1)}.jsonl", [title(NEWER), *ASKED[1:]], 200)
    write(bundles / f"{session('a', 2)}.jsonl", [title(NEWER), *ended[1:]], 100)
    write(bundles / f"{session('b')}.jsonl", [title(BUNDLE), prompt("go on")], 50)
    write(bundles / f"{session('b', 2)}.jsonl", ended, 300)
    (bundles / f"{session('b', 3)}.jsonl").symlink_to(bundles / f"{session('b', 2)}.jsonl")
    write(bundles / "b-named.jsonl", [title(BUNDLE), *ASKED[1:]], 40)
    write(bundles / f"{session('c')}.jsonl", [title(RUNNING), *ASKED[1:]], 50)
    write(bundles / f"{session('d')}.jsonl", [*ASKED[1:], result()], 60, cut.TAIL)
    write(bundles / f"{session('e')}.jsonl", [title(GONE), *ASKED[1:]], 50)
    write(bundles / f"{session('f')}.jsonl", [title("Branch status"), *ASKED[1:]], 50)
    for name, entries, age in (
        (OPS_CUT, [*ASKED[1:], call(command="hctl job 3")], 70),
        (OPS_TAKEN, ASKED[1:], 80),
        (OPS_LIVE, ASKED[1:], 10),
        (OPS_OLD, ASKED[1:], cut.WINDOW + 60),
        (OPS_ENDED, [*ASKED[1:], result(), answer("Nothing left: done.")], 10),
    ):
        entries = [{"type": "ai-title", "aiTitle": "Ops"}, *entries]
        write(projects / "-repo" / f"{name}.jsonl", entries, age)
    for bundle in (NEWER, BUNDLE, RUNNING, MAPPED):
        (src / bundle / ".agent").mkdir(parents=True)
    (src / MAPPED / ".agent" / "session").write_text(f"{session('d')}\n")
    (src / MAPPED / ".agent" / "priority").write_text("5\n")
    (src / MAPPED / ".agent" / "model").write_text("sonnet\n")
    (src / BUNDLE / ".agent" / "done").write_text("0\n")
    daemon.PROJECTS = projects
    daemon.HOST_FOLDER = str(root / "repo" / "host-ctl")
    daemon.CONFIG["REPO_ROOT"] = "/repo"
    daemon.HELD_DIR = root / "held"
    daemon.get_worktree_container_folder = lambda: str(root / "src")
    daemon.get_worktree_bundle_folder = lambda name: str(src / name)
    daemon.get_open_windows = lambda: {str(src / RUNNING)}
    daemon.get_agent_folder = lambda name: (
        root / "ops" / name if name.startswith(daemon.OPS) else src / name / ".agent"
    )
    cut.get_live_sessions = lambda records: {OPS_LIVE}


def check_find(root):
    """The newest session of each bundle here whose container is down, and each ops session no
    claude runs, written in the window; the last prompt typed even when the tail lacks it."""
    rows = daemon.find_cut()
    found = [(row["name"], row["session"], row["why"]) for row in rows]
    expected = [
        (BUNDLE, session("b"), "a prompt had no answer"),
        (MAPPED, session("d"), "a tool result had no answer"),
        (f"ops-{OPS_CUT}", OPS_CUT, "a tool call ran"),
        (f"ops-{OPS_TAKEN}", OPS_TAKEN, "a tool call ran"),
    ]
    checks = [
        ("the cut sessions, newest first", found == expected),
        ("the last prompt and call out of the tail", (rows[1]["prompt"], rows[1]["call"]) == (
            "fix it", "Bash git push",
        )),
        ("the last call", rows[2]["call"] == "Bash hctl job 3"),
        ("the title", (rows[0]["title"], rows[2]["title"]) == (BUNDLE, "Ops")),
    ]
    return [f"find: {name}: {found}" for name, passed in checks if not passed], len(checks)


def check_hold(root):
    """One request for every cut session, refused to a bundle; the same one asked again takes the
    place of the one that waits."""
    _code, held = daemon.open_bundle(daemon.HOST, {}, {"cut": True, "resume": True})
    item = json.loads((daemon.HELD_DIR / "1.json").read_text())
    sent = item["send"]["resume"]
    checks = [
        ("it is held as b1", held["held"] == "b1"),
        ("its title counts them", item["title"] == "resume 4 sessions a crash or a restart cut"),
        ("it lists each", [line for line in item["lines"] if not line.startswith(" ")][:4] == [
            BUNDLE,
            MAPPED,
            f"ops-{OPS_CUT}: Ops",
            f"ops-{OPS_TAKEN}: Ops",
        ]),
        ("it shows a prompt", cut.GO_ON in item["lines"]),
        ("it sends each session", [entry["session"] for entry in sent] == [
            session("b"), session("d"), OPS_CUT, OPS_TAKEN,
        ]),
        ("each prompt ends on its last call", sent[2]["prompt"].endswith(": Bash hctl job 3")),
    ]
    refusals = [
        ("bundle", {"cut": True, "resume": True}, "cut: only the host and the ops container"),
        (daemon.HOST, {"cut": True}, "cut: only with resume"),
        (daemon.HOST, {"cut": True, "resume": True, "task": "x"}, "cut: only with resume"),
    ]
    for caller, body, error in refusals:
        try:
            daemon.open_bundle(caller, {}, body)
            found = None
        except (ValueError, daemon.Refused) as refusal:
            found = str(refusal)
        checks.append((f"{caller} {body} is refused", (found or "").startswith(error)))
    try:
        daemon.list_cut("bundle", {}, {})
        checks.append(("a bundle cannot list them", False))
    except daemon.Refused:
        checks.append(("a bundle cannot list them", True))
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    daemon.identify = lambda connection: daemon.HOST
    code, lines = run_client(path, "open", "--cut")
    checks.append(("the client lists them", (code, lines[0], lines[-1]) == (
        0, BUNDLE, "4 sessions cut in the last 3 days: bctl open --cut --resume",
    )))
    code, lines = run_client(path, "open", "--cut", "--resume")
    expected = (0, "waits for Seb: hok b1. His yes resumes these 4 sessions.")
    checks.append(("the client asks again, under the same number", (code, lines[-1]) == expected))
    for args in (("open",), ("open", BUNDLE, "--cut"), ("open", "--cut", "--terminal")):
        code, lines = run_client(path, *args)
        checks.append((f"bctl {' '.join(args)} is refused", code == 2 and "--cut" in lines[-1]))
    server.shutdown()
    server.server_close()
    return [f"hold: {name}" for name, passed in checks if not passed], len(checks)


def check_yes(root):
    """Seb's yes: an ops session opens in an ops agent window on its prompt, a bundle session is
    queued on its prompt and its session, and one that runs again since is left."""
    opened, state = [], {"launches": {}, "queue": []}
    cut.get_live_sessions = lambda records: {OPS_LIVE, OPS_CUT}
    daemon.get_transcript = lambda session: True
    daemon.is_agent_running = lambda name: False
    daemon.open_terminal = lambda log, name: opened.append(
        (name, (daemon.get_agent_folder(name) / "task.md").read_text()),
    )
    daemon.start_job = lambda caller, verb, work: (202, work(None))
    daemon.read_state = lambda: state
    daemon.write_state = lambda state: None
    daemon.get_bundle_container = lambda bundle: None
    daemon.read_disk_free = lambda: 100
    daemon.session_env = lambda: {"DISPLAY": ":0"}
    daemon.describe_memory = lambda folders: {"short": False}
    daemon.MAX_AGENTS = 8
    path = daemon.HELD_DIR / "1.json"
    item = daemon.HELD.read_json(path)
    path.with_suffix(".answer").write_text(f"yes {item['digest']}\n")
    daemon.HELD.take(path.with_suffix(".answer"), daemon.send_held)
    text = daemon.HELD.read_json(path.with_suffix(".result"))["text"]
    prompts = {entry["name"]: entry["prompt"] for entry in item["send"]["resume"]}
    agent = daemon.get_agent_folder(BUNDLE)
    queued = [
        (entry["bundle"], entry.get("session"), entry["priority"], entry["model"])
        for entry in state["queue"]
    ]
    checks = [
        ("hok says what ran", text == (
            f"ops agent windows open on ops-{OPS_TAKEN}; queued to go on in their agent terminal:"
            f" {BUNDLE}, {MAPPED}; left as they are: ops-{OPS_CUT} (it runs again, or its turn"
            " ended since)"
        )),
        ("each opens on its prompt", opened == [
            (BUNDLE, prompts[BUNDLE]),
            (f"ops-{OPS_TAKEN}", prompts[f"ops-{OPS_TAKEN}"]),
        ]),
        ("a bundle goes on with its session", read_text(agent / "session") == session("b")),
        ("its last run goes to its history", not (agent / "done").exists()),
        ("the next waits with its session, priority and model", queued == [
            (MAPPED, session("d"), 5, "sonnet"),
        ]),
    ]
    return [f"yes: {name}" for name, passed in checks if not passed], len(checks)


def main():
    failures, total = [], 0
    with tempfile.TemporaryDirectory() as folder:
        root = pathlib.Path(folder)
        for check in (check_cases, check_live):
            failed, count = check(root)
            failures += failed
            total += count
        prepare(root)
        for check in (check_find, check_hold, check_yes):
            failed, count = check(root)
            failures += failed
            total += count
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
