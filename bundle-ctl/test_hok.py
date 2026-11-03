#!/usr/bin/env python3
"""What GET /hok answers each caller, from a stand-in for the approve.py of host-ctl that
hok-waiting.py loads: python3 test_hok.py
"""

import json
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon  # noqa: E402

LISTED = [
    {"caller": "master-x--seb", "name": "g12", "session": "s9"},
    {"caller": "ops", "name": "30+31", "session": "s1"},
    {"caller": "ops", "name": "t4", "session": ""},
    {"caller": "master-y--seb", "name": "f3", "session": ""},
]
APPROVE = """
import functools, json, pathlib, sys, time, types
root = pathlib.Path(__file__).parent
loaded = []
with open(root / "loads", "a") as file:
    file.write("1")
mounts = types.SimpleNamespace(read=lambda: {{}})


@functools.lru_cache
def get_containers():
    return ()


def get_version():
    return (root / "version").read_text()


def is_stale():
    return get_version() != loaded[0]


def list_waiting():
    with open(root / "reads", "a") as file:
        file.write("1")
    time.sleep(float((root / "sleep").read_text()))
    return json.loads({listed!r})
"""


def get_names(caller, query):
    return [row["name"] for row in daemon.list_hok(caller, query, {})[1]["requests"]]


def count(root, name):
    path = pathlib.Path(root, name)
    return len(path.read_text()) if path.exists() else 0


def read_again():
    daemon.hok_read["at"] = -daemon.HOK_KEPT
    return daemon.read_hok_waiting()


def get_proposal(caller, name):
    try:
        return daemon.get_proposal(caller, {"name": [name]}, {})[1]
    except (ValueError, daemon.Refused) as error:
        return type(error).__name__


def check_proposals(root, failures):
    """What GET /proposal says of a proposal in the handoff inbox."""
    daemon.HANDOFF = inbox = pathlib.Path(root, "handoff")
    (inbox / "master-x--seb").mkdir(parents=True)
    (inbox / "master-x--seb" / "WHY").write_text("better\n")
    (inbox / "master-y--seb").mkdir()
    (inbox / "master-y--seb.refused").write_text("too big\n")
    (inbox / "master-z--seb.written").write_text("\n")
    (inbox / "master-w--seb@memory").symlink_to(inbox / "master-x--seb")
    cases = [
        ("a card waits", get_proposal("master-x--seb", "master-x--seb"), "waits"),
        ("a no and its reason", get_proposal("master-y--seb", "master-y--seb"), "refused"),
        ("a whole yes", get_proposal("master-z--seb", "master-z--seb"), "written"),
        ("nothing reached hok", get_proposal("master-v--seb", "master-v--seb"), "none"),
        ("a link is no card", get_proposal("master-w--seb", "master-w--seb@memory"), "none"),
        ("not its own", get_proposal("master-x--seb", "master-y--seb"), "Refused"),
        ("no bundle name", get_proposal(daemon.HOST, "../x"), "ValueError"),
        ("the host asks any", get_proposal(daemon.HOST, "master-y--seb"), "refused"),
    ]
    for name, found, expected in cases:
        if (found.get("state") if isinstance(found, dict) else found) != expected:
            failures.append(f"proposal, {name}: {found!r}, expected {expected!r}")
    said = get_proposal("master-y--seb", "master-y--seb")
    if said.get("text") != "too big" or not said.get("at"):
        failures.append(f"proposal, a no gives its reason and when: {said!r}")


def main():
    failures = []
    with tempfile.TemporaryDirectory() as root:
        script = pathlib.Path(root, "approve.py")
        script.write_text(APPROVE.format(listed=json.dumps(LISTED)))
        pathlib.Path(root, "version").write_text("1")
        pathlib.Path(root, "sleep").write_text("0")
        daemon.HOK_WAITING = script
        cases = [
            ("a bundle gets its own", get_names("master-x--seb", {}), ["g12"]),
            (
                "the host those of its ops session",
                get_names(daemon.HOST, {"session": ["s1"]}),
                ["30+31"],
            ),
            ("a bundle with none gets none", get_names("master-z--seb", {}), []),
        ]
        for name, found, expected in cases:
            if found != expected:
                failures.append(f"{name}: {found!r}, expected {expected!r}")
        if count(root, "reads") != 1:
            failures.append(f"hok waiting read {count(root, 'reads')} times in {daemon.HOK_KEPT} s")
        read_again()
        if (count(root, "loads"), count(root, "reads")) != (1, 2):
            failures.append(f"a read after {daemon.HOK_KEPT} s loaded hok again")
        pathlib.Path(root, "version").write_text("2")
        read_again()
        read_again()
        if count(root, "loads") != 2:
            failures.append(f"new code of hok: loaded {count(root, 'loads')} times, expected 2")
        daemon.HOK_TIME = 0.5
        pathlib.Path(root, "sleep").write_text("2")
        started = time.monotonic()
        try:
            read_again()
            failures.append("a worker with no answer gave a list")
        except daemon.Refused:
            pass
        if time.monotonic() - started > 1.5:
            failures.append("a worker with no answer held the read past HOK_TIME")
        pathlib.Path(root, "sleep").write_text("0")
        if read_again() != LISTED or count(root, "loads") != 3:
            failures.append("no new worker after one that did not answer")
        try:
            daemon.list_hok(daemon.HOST, {}, {})
            failures.append("the host with no session got an answer")
        except ValueError:
            pass
        daemon.HOK_WAITING = pathlib.Path(root, "none.py")
        try:
            daemon.list_hok("master-x--seb", {}, {})
            failures.append("an answer with no host-ctl")
        except daemon.Refused:
            pass
        daemon.hok_read.pop("worker").kill()
        check_proposals(root, failures)
    for line in failures:
        print(line)
    print(f"{20 - len(failures)}/20 cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
