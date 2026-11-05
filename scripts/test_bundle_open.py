#!/usr/bin/env python3
"""What an odoo-bundle:// link opens, with fakes of the desktop commands:
python3 test_bundle_open.py

A link comes from any text an agent prints. A name that is no bundle name opens nothing, even
when it leads to a folder with a dev container config (security check 11-03, C5).
"""

import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent


def main():
    failures = []
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name).resolve()
        fakes, log = root / "bin", root / "log"
        fakes.mkdir()
        for command in ("code", "gnome-terminal", "notify-send", "podman", "wmctrl", "xdg-open"):
            (fakes / command).write_text(f'#!/bin/sh\necho {command} "$@" >> {log}\n')
            (fakes / command).chmod(0o755)
        config = root / "repo" / "TestWarden" / "engine" / ".cache" / ".devcontainer"
        config.mkdir(parents=True)
        (config / "devcontainer.json").write_text('{"initializeCommand": "touch /tmp/ran"}\n')
        (root / "src" / "master").mkdir(parents=True)
        env = {
            **os.environ,
            "HOME": str(root),
            "PATH": f"{fakes}:{os.environ['PATH']}",
            "REPO_ROOT": str(root / "repo"),
            "WORKTREE_ROOT": str(root / "src"),
            "XDG_STATE_HOME": str(root / "state"),
        }
        up = "../" * 12
        target = urllib.parse.quote(f"x/{up}{str(config.parent).lstrip('/')}")
        for link, expected in (
            (f"odoo-bundle://{target}/", ["notify-send", "not the name of a bundle"]),
            ("odoo-bundle://../../repo/TestWarden/engine/.cache", ["notify-send", "not the name"]),
            ("odoo-bundle://.hidden", ["notify-send", "not the name of a bundle"]),
        ):
            log.write_text("")
            subprocess.run(
                [sys.executable, str(HERE / "bundle_open.py"), link],
                capture_output=True,
                check=False,
                env=env,
                timeout=60,
            )
            ran = log.read_text().splitlines()
            if len(ran) != 1 or not all(word in ran[0] for word in expected):
                failures.append(f"{link}: ran {ran}, expected one {' '.join(expected)}")
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import commands;"
            " print(commands.get_worktree_bundle_folder(sys.argv[2]))"
        )
        res = subprocess.run(
            [sys.executable, "-c", code, str(HERE), f"master/{up}tmp"],
            capture_output=True,
            check=False,
            env=env,
            text=True,
        )
        if res.returncode == 0:
            failures.append(f"a folder out of the worktree root: {res.stdout.strip()}")
    for line in failures:
        print(line)
    print(f"{4 - len(failures)}/4 cases as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
