#!/usr/bin/env python3
"""wait, on-unlock and here replace a link left in request/ and follow none, with dbus faked:
python3 test_request.py"""

import os
import pathlib
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent


def run(root, script):
    env = {"HOME": str(root), "PATH": f"{root / 'bin'}:{os.environ['PATH']}"}
    subprocess.run(["sh", str(HERE / script)], capture_output=True, check=False, env=env, timeout=30)


def fake(root, name, text):
    path = root / "bin" / name
    path.write_text(f"#!/bin/sh\necho '{text}'\n")
    path.chmod(0o755)


def main():
    found = {}
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        state = root / ".local" / "state" / "ssh-github-mux"
        request, sock = state / "request", state / "sock"
        for folder in (request, sock, root / "bin"):
            folder.mkdir(parents=True)
        victim = root / "victim"
        victim.write_text("kept\n")
        (request / "now").symlink_to(victim)
        (sock / "stopped").touch()
        os.utime(sock / "stopped", (time.time() + 60,) * 2)
        run(root, "wait")
        found["wait: the file a link names"] = (victim.read_text(), "kept\n")
        found["wait: request/now"] = ((request / "now").is_symlink(), False)
        found["wait: the git command in it"] = ("python3" in (request / "now").read_text(), True)

        (request / "now").unlink()
        (request / "now").symlink_to(victim)
        (request / "held").touch()
        fake(root, "dbus-monitor", "boolean false")
        run(root, "on-unlock")
        found["on-unlock: the file a link names"] = (victim.read_text(), "kept\n")
        found["on-unlock: request/now"] = (
            (request / "now").read_text(),
            "the screen was unlocked\n",
        )

        (request / "held").unlink()
        (request / "held").symlink_to(root / "made")
        fake(root, "dbus-send", "boolean true")
        run(root, "here")
        found["here: no file where a link points"] = ((root / "made").exists(), False)
        found["here: request/held"] = ((request / "held").is_file(), True)
        found["no temporary file left"] = (sorted(path.name for path in state.iterdir()), [
            "request",
            "sock",
        ])
    failures = [
        f"{case}: {got!r}, expected {expected!r}"
        for case, (got, expected) in found.items()
        if got != expected
    ]
    for line in failures:
        print(line)
    print(f"{len(found) - len(failures)}/{len(found)} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
