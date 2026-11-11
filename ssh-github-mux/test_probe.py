#!/usr/bin/env python3
"""probe restarts a connection that opens no new session, here lets that restart through a
held start, and install links the units, with ssh, timeout and systemctl faked:
python3 test_probe.py"""

import os
import pathlib
import socket
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ANSWER = "Hi seb-odoo! You've successfully authenticated, but GitHub does not provide shell access."


def fake(root, name, text):
    path = root / "bin" / name
    path.write_text(f"#!/bin/sh\n{text}\n")
    path.chmod(0o755)


def run(root, script):
    env = {"HOME": str(root), "PATH": f"{root / 'bin'}:{os.environ['PATH']}"}
    return subprocess.run(
        ["sh", str(HERE / script)],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=30,
    )


def read(path):
    return path.read_text() if path.exists() else ""


def main():
    found = {}
    with tempfile.TemporaryDirectory(dir="/tmp") as name:
        root = pathlib.Path(name)
        sock = root / ".local" / "state" / "ssh-github-mux" / "sock"
        calls = root / "calls"
        for folder in (sock, root / "bin"):
            folder.mkdir(parents=True)
        for program in ("systemctl", "notify-send"):
            fake(root, program, f'echo "{program} $*" >> {calls}')
        fake(root, "timeout", 'shift; exec "$@"')
        fake(root, "ssh", f'echo "ssh $*" >> {calls}; echo "{ANSWER}" >&2; exit 1')

        run(root, "probe")
        found["no socket: nothing asked"] = (read(calls), "")

        server = socket.socket(socket.AF_UNIX)
        server.bind(str(sock / "github"))
        run(root, "probe")
        found["an answer: one session through the socket"] = (
            f"ControlPath={sock / 'github'}" in read(calls) and "ProxyCommand=false" in read(calls),
            True,
        )
        found["an answer: no restart"] = ("systemctl" in read(calls), False)

        calls.unlink()
        fake(root, "timeout", "exit 124")
        res = run(root, "probe")
        found["no answer: the connection restarts"] = (
            read(calls),
            "systemctl --user restart --no-block ssh-github-mux.service\n",
        )
        found["no answer: the journal says it"] = ("no new session in 20 s" in res.stdout, True)
        found["no answer: here lets it through"] = ((sock / "probe").exists(), True)

        calls.unlink()
        run(root, "probe")
        found["no answer twice: Seb is told"] = (read(calls).startswith("notify-send"), True)

        fake(root, "timeout", 'shift; exec "$@"')
        run(root, "probe")
        found["an answer again: the count starts over"] = ((sock / "probe-failed").exists(), False)
        server.close()

        request = sock.parent / "request"
        request.mkdir()
        (request / "held").touch()
        found["here: a probe restart passes a held start"] = (run(root, "here").returncode, 0)
        found["here: once"] = ((sock / "probe").exists(), False)
        found["here: a held start holds the rest"] = (run(root, "here").returncode, 1)

        calls.unlink()
        fake(root, "systemctl", f'echo "systemctl $*" >> {calls}; [ "$2" != is-active ]')
        res = run(root, "install")
        units = root / ".config" / "systemd" / "user"
        found["install: the units are links to this folder"] = (
            sorted(
                path.name
                for path in units.iterdir()
                if path.is_symlink() and path.resolve().parent == HERE
            ),
            [
                "ssh-github-mux-probe.service",
                "ssh-github-mux-probe.timer",
                "ssh-github-mux-unlock.service",
                "ssh-github-mux.path",
                "ssh-github-mux.service",
            ],
        )
        found["install: a connection down starts no probe"] = (
            read(calls),
            "systemctl --user daemon-reload\nsystemctl --user is-active -q ssh-github-mux.service\n",
        )
        calls.unlink()
        fake(root, "systemctl", f'echo "systemctl $*" >> {calls}')
        run(root, "install")
        found["install: a connection up starts its probe"] = (
            read(calls).splitlines()[-1:],
            ["systemctl --user start ssh-github-mux-probe.timer"],
        )
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
