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
    for line in failures:
        print(line)
    cases = len(CASES) * 2 + 2
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
