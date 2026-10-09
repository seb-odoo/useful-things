#!/usr/bin/env python3
"""The model and the effort a task may ask for its agent, without podman: python3 test_options.py"""

import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon

EFFORT = "effort: one of low, medium, high, xhigh"
MODEL = "model: one of haiku, opus, sonnet"

REFUSED = [
    ({"effort": "high"}, "effort: only with a task"),
    ({"model": "sonnet"}, "model: only with a task"),
    ({"task": "do", "effort": "max"}, EFFORT),
    ({"task": "do", "effort": ["high"]}, EFFORT),
    ({"task": "do", "model": "claude-fable-5-1"}, MODEL),
    ({"task": "do", "model": "default"}, MODEL),
    ({"task": "do", "model": "fable"}, MODEL),
]
WRITTEN = [
    ({}, (None, None)),
    ({"effort": "medium", "model": "opus"}, (None, None)),
    ({"model": "sonnet"}, ("sonnet", None)),
    ({"effort": "low", "model": "haiku"}, ("haiku", "low")),
    ({"effort": "xhigh"}, (None, "xhigh")),
    ({}, (None, None)),
]
STARTED = [
    ((None, None), "opus medium"),
    (("sonnet", "high"), "sonnet high"),
    (("haiku", "low"), "haiku low"),
    (("opus", "xhigh"), "opus xhigh"),
    (("fable", "max"), "opus medium"),
    (("default", ""), "opus medium"),
    (("sonnet fable", "high max"), "opus medium"),
]


def refusal(body):
    """Why the daemon refuses a request with this body, None when it takes it."""
    daemon.read_state = lambda: {"launches": {}, "queue": []}
    try:
        daemon.check_task("bundle", body)
    except ValueError as error:
        return str(error)
    return None


def written(folder, body):
    """Queue the task of this body and write it: what `.agent/model` and `.agent/effort` hold,
    None for no file.
    """
    state = {"launches": {}, "queue": []}
    daemon.read_state = lambda: state
    daemon.enqueue("host", "bundle", {"task": "do", **body})
    daemon.write_task(state["queue"][0])
    return daemon.read_text(folder / "model"), daemon.read_text(folder / "effort")


def started(folder, files):
    """Run the part of agent-run.sh that reads the two files: the model and effort it picks."""
    for name, text in zip(("model", "effort"), files):
        if text is not None:
            (folder / name).write_text(f"{text}\n")
    script = (HERE / "client" / "agent-run.sh").read_text()
    picks = re.search(
        r'^case \$\(cat "\$agent/model".*?^esac\ncase .*?^esac\n',
        script,
        re.MULTILINE | re.DOTALL,
    )
    return subprocess.run(
        ["bash", "-c", f'agent={folder}\n{picks[0]}echo "$model $effort"'],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


def main():
    daemon.get_agent = lambda bundle, alive=True: None
    daemon.get_open_bundle_folders = list
    daemon.launch_queued = lambda: None
    daemon.write_state = lambda state: None
    failures = []
    for body, expected in REFUSED:
        found = refusal(body)
        if found != expected:
            failures.append(f"{body}: refused with {found!r}, expected {expected!r}")
    with tempfile.TemporaryDirectory() as root:
        folder = pathlib.Path(root) / ".agent"
        folder.mkdir()
        daemon.get_agent_folder = lambda bundle: folder
        for body, expected in WRITTEN:
            found = (refusal({"task": "do", **body}), written(folder, body))
            if found != (None, expected):
                failures.append(f"{body}: wrote {found}, expected {(None, expected)}")
    for files, expected in STARTED:
        with tempfile.TemporaryDirectory() as root:
            found = started(pathlib.Path(root), files)
        if found != expected:
            failures.append(f"{files}: started on {found!r}, expected {expected!r}")
    for line in failures:
        print(line)
    cases = len(REFUSED) + len(WRITTEN) + len(STARTED)
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
