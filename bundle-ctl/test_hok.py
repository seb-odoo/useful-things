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
    for line in failures:
        print(line)
    print(f"{11 - len(failures)}/11 cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
