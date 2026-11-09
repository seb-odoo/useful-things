#!/usr/bin/env python3
"""What the card that frees the disk lists, and what its yes removes, with podman faked:
python3 test_free_disk.py"""

import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon

PS = "c1 /home/seb/src/odoo/master/master-a--seb\nc2 /home/seb/src/odoo/19.0/19.0-b\n"
IMAGES = "i1 1.01 GB\n"
CASES = [
    (20, PS, IMAGES, "20 GB freed, 40 GB left"),
    (5, PS, IMAGES, "20 GB freed, 25 GB left: still under 30, an ops agent frees the rest"),
    (20, "", IMAGES, "10 GB freed, 30 GB left"),
]
LINES = [
    "20 GB left on the disk, 30 needed for new bundles and agents",
    "2 stopped containers of bundles, made again at the next open:",
    "  master-a--seb",
    "  19.0-b",
    "1 podman images that no tag names:",
    "  i1 1.01 GB",
]


REMOTE = (
    "this agent runs on sebpc, its /workspace there: 2.0 of 59 GB free. sebpc drops the checkouts"
    " of the agents that ended once under 6 GB, and no space of the laptop frees it: no card"
)
DISK = {
    "file_systems": [
        {"path": "/var/lib/remote-runs", "size_gb": 195.8, "free_gb": 31.29},
        {"path": "/var/lib/remote-runs-fast", "size_gb": 58.76, "free_gb": 2.01},
    ],
    "checkouts": "/var/lib/remote-runs-fast",
    "floor_gb": 30.0,
    "fast_floor_gb": 6.0,
}


class FakeWorkers:
    def look(self, name, what, lines):
        return DISK if (name, what) == ("sebpc", "disk") else {}


def fake_podman(calls, disk, listed):
    def run(command, **kwargs):
        calls.append(command[1:])
        if command[1] in ("rm", "rmi"):
            disk[0] += 10
        return subprocess.CompletedProcess(command, 0, listed.get(command[1], ""), "")

    return run


def main():
    failures = []
    with tempfile.TemporaryDirectory() as name:
        daemon.HELD_DIR = pathlib.Path(name) / "held"
        daemon.WORKERS = FakeWorkers()
        daemon.remote_agent.read_remote = lambda folder: (
            {"worker": "sebpc"} if folder.name == "master-remote--seb" else None
        )
        daemon.DISK_STOP = 30
        for free, ps, images, expected in CASES:
            calls, disk = [], [free]
            daemon.read_disk_free = lambda disk=disk: disk[0]
            daemon.subprocess.run = fake_podman(calls, disk, {"ps": ps, "images": images})
            _code, answer = daemon.free_disk("master-x--seb", {}, {"reason": "build refused"})
            number = answer["held"].removeprefix(daemon.HELD_LETTER)
            held = json.loads((daemon.HELD_DIR / f"{number}.json").read_text())
            if free == 20 and ps and held["lines"] != LINES:
                failures.append(f"the card: {held['lines']}")
            reply = daemon.HELD_DIR / f"{number}.answer"
            reply.write_text(f"yes {held['digest']}\n")
            daemon.HELD.take(reply, daemon.send_held)
            found = json.loads((daemon.HELD_DIR / f"{number}.result").read_text())["text"]
            if found != expected:
                failures.append(f"yes at {free} GB: {found!r}, expected {expected!r}")
            removed = [call for call in calls if call[0] in ("rm", "rmi")]
            ids = [*(["rm", "c1", "c2"] for _ in ps[:1]), ["rmi", "i1"]]
            if removed != ids:
                failures.append(f"yes at {free} GB removes {removed}, expected {ids}")
        daemon.read_disk_free = lambda: 20
        daemon.subprocess.run = fake_podman([], [20], {})
        try:
            daemon.free_disk("master-x--seb", {}, {})
            found = "held"
        except daemon.Refused as error:
            found = str(error)
        if found != "20 GB left, and nothing to remove here: an ops agent frees space":
            failures.append(f"nothing to remove: {found}")
        held = sorted(daemon.HELD_DIR.iterdir())
        try:
            daemon.free_disk("master-remote--seb", {}, {})
            found = "held"
        except daemon.Refused as error:
            found = str(error)
        if found != REMOTE or sorted(daemon.HELD_DIR.iterdir()) != held:
            failures.append(f"an agent on a worker: {found}")
    for line in failures:
        print(line)
    cases = len(CASES) * 2 + 3
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
