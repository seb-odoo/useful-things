#!/usr/bin/env python3
"""Prepare the host for the start of a bundle container, the initializeCommand of base.jsonc.

    python3 initialize.py <bundle folder name> <bundle folder>

base.jsonc calls it as an array, so that no shell reads the name of the bundle. It makes the
folders the container mounts, as a missing mount source stops the container from starting, links
the node_modules of the base, then runs claude-config.py, extensions.py and bundle-db.py, and stops
at the first that fails. warden-chrome.py runs before them and never stops a start: a bundle with
no browser for its Hoot runs still starts.
"""

import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config  # noqa: E402

# Where base.jsonc mounts the node_modules of the base, read-only.
INSIDE = "/opt/node_modules"


def link_node_modules(folder):
    """Make the node_modules of odoo and enterprise a link to the one of the base. A folder of
    their own goes, hard links from before included, through which a bundle writes the files of
    the others. No link is followed on the way, as the container writes the bundle folder."""
    for repo in ("odoo", "enterprise"):
        try:
            fd = os.open(folder / repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            continue
        path = f"/proc/self/fd/{fd}/node_modules"
        try:
            if os.path.islink(path) and os.readlink(path) == INSIDE:
                continue
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            elif os.path.lexists(path):
                os.unlink(path)
            os.symlink(INSIDE, "node_modules", dir_fd=fd)
        except OSError as error:
            print(f"{repo}/node_modules is left as it is: {error}", file=sys.stderr)
        finally:
            os.close(fd)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip())
    name, folder = sys.argv[1:]
    values = config.load()
    for path in (
        pathlib.Path(values["REPO_ROOT"]) / "TestWarden" / "engine" / ".cache",
        pathlib.Path(values["STATE_ROOT"]) / "bundle-ctl" / "sock",
        pathlib.Path(values["STATE_ROOT"]) / "ssh-github-mux" / "request",
        pathlib.Path(values["STATE_ROOT"]) / "ssh-github-mux" / "sock",
    ):
        path.mkdir(parents=True, exist_ok=True)
    (pathlib.Path(folder).parent / "node_modules").mkdir(exist_ok=True)
    link_node_modules(pathlib.Path(folder))
    subprocess.run([sys.executable, str(HERE / "warden-chrome.py")], check=False)
    for script, *args in (
        ("claude-config.py", name),
        ("extensions.py", name, folder),
        ("bundle-db.py", name),
    ):
        res = subprocess.run([sys.executable, str(HERE / script), *args], check=False)
        if res.returncode:
            sys.exit(res.returncode)


if __name__ == "__main__":
    main()
