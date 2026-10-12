#!/usr/bin/env python3
"""What `bctl add-repo` runs on the host and what it refuses, without git:
python3 test_add_repo.py
"""

import pathlib
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon  # noqa: E402
from test_queue import run_client  # noqa: E402

BUNDLE = "master-x--seb"
OTHER = "master-y--seb"


def check(root):
    world = {"caller": BUNDLE, "calls": []}
    for bundle in (BUNDLE, OTHER):
        (root / "master" / bundle / "odoo").mkdir(parents=True)
        (root / "master" / bundle / daemon.MADE).touch()
    daemon.STATE = root
    daemon.get_worktree_bundle_folder = lambda bundle: str(root / "master" / bundle)
    daemon.get_worktree_bundle_repo_folder = lambda bundle, repo: str(
        root / "master" / bundle / repo,
    )
    daemon.identify = lambda connection: world["caller"]
    daemon.is_in_container = lambda connection: world["caller"] != daemon.HOST
    daemon.run_script = lambda log, script, *args: world["calls"].append((script, *args))
    path = root / "ctl.sock"
    server = daemon.Server(str(path), daemon.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    code, _lines = run_client(path, "add-repo", "documentation")
    checks.append(
        (
            "a bundle adds a repo to itself",
            (code, world["calls"]) == (0, [("add_repo.py", BUNDLE, "documentation")]),
        ),
    )
    code, lines = run_client(path, "add-repo", "documentation", OTHER)
    checks.append(
        (
            "a bundle cannot add one to another bundle",
            (code, lines) == (1, ["bctl: 409 a bundle adds a repo to itself only"]),
        ),
    )
    code, lines = run_client(path, "add-repo", "odoo")
    checks.append(
        (
            "a repo already there is refused",
            (code, lines) == (1, [f"bctl: 409 odoo is already in {BUNDLE}"]),
        ),
    )
    code, lines = run_client(path, "add-repo", "themes")
    checks.append(
        (
            "an unknown repo is refused",
            code == 1 and lines[0].startswith("bctl: 400 repo must be one of design-themes, "),
        ),
    )
    world["caller"] = daemon.HOST
    code, _lines = run_client(path, "add-repo", "design-themes", OTHER)
    checks.append(
        (
            "the host names the bundle",
            (code, world["calls"][-1]) == (0, ("add_repo.py", OTHER, "design-themes")),
        ),
    )
    code, lines = run_client(path, "add-repo", "design-themes")
    checks.append(
        ("the host without a bundle is refused", (code, lines) == (1, ["bctl: 400 no bundle "])),
    )
    server.shutdown()
    server.server_close()
    return [name for name, passed in checks if not passed], len(checks)


def main():
    with tempfile.TemporaryDirectory() as root:
        failures, total = check(pathlib.Path(root))
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
