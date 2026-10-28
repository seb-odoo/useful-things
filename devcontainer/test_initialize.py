#!/usr/bin/env python3
"""What initialize.py does before the start of a bundle container, with the scripts it runs faked:
python3 test_initialize.py"""

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402

NAME = "a b;touch shell-ran"
FAKE = """import json, sys
with open(sys.argv[0] + ".calls", "a") as file:
    file.write(json.dumps(sys.argv[1:]) + "\\n")
"""
PACKAGE = b'{"devDependencies": {"eslint": "^10.12.0"}}\n'
TARGET = f"/opt/node_modules/{hashlib.sha256(PACKAGE).hexdigest()[:16]}/node_modules"
FAKE_NPM = """#!/bin/sh
echo "$*" >> "$0.calls"
"""


def initialize(root, folder):
    env = {
        "HOME": str(root / "home"),
        "PATH": os.environ["PATH"],
        "REPO_ROOT": str(root / "repo"),
        "STATE_ROOT": str(root / "state"),
        "WORKTREE_ROOT": str(root / "src"),
    }
    return subprocess.run(
        [sys.executable, str(root / "tools" / "initialize.py"), NAME, str(folder)],
        capture_output=True,
        check=False,
        cwd=root,
        env=env,
        text=True,
    )


def npm(root, folder, args, proxy="bundle"):
    """`npm ARGS` in the odoo of the bundle, in a shell of the container."""
    bashrc = HERE.parent / "container-rw" / "devcontainer.bashrc"
    env = {"HOME": str(root / "home"), "PATH": f"{root / 'bin'}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-c", f'source "{bashrc}" && npm {args}'],
        capture_output=True,
        check=False,
        cwd=folder / "odoo",
        env={**env, "ODOO_PROXY_HOST": proxy} if proxy else env,
        text=True,
    ).returncode


def check_npm(root, folder, check):
    """The npm of the bashrc: the reload of odoo's tooling gets the link of initialize.py."""
    (root / "bin").mkdir()
    (root / "bin" / "npm").write_text(FAKE_NPM)
    (root / "bin" / "npm").chmod(0o755)
    link = folder / "odoo" / "node_modules"
    link.unlink()
    check("npm install, no tree yet", npm(root, folder, "install"), 1)
    check("npm install links", os.readlink(link), TARGET)
    check("npm run", npm(root, folder, "run format-staged"), 0)
    check("npm install --save-dev x", npm(root, folder, "install --save-dev x"), 0)
    check("npm install on the host", npm(root, folder, "install", proxy=None), 0)
    calls = (root / "bin" / "npm.calls").read_text().splitlines()
    check("real npm", calls, ["run format-staged", "install --save-dev x", "install"])


def main():
    failures = []

    def check(what, got, expected):
        if got != expected:
            failures.append(f"{what}: got {got!r}, expected {expected!r}")

    base_config = build.read_jsonc(HERE / "base.jsonc")
    check(
        "initializeCommand",
        base_config["initializeCommand"],
        [
            "python3",
            "@USEFUL_THINGS@/devcontainer/initialize.py",
            "${localWorkspaceFolderBasename}",
            "${localWorkspaceFolder}",
        ],
    )
    mount = "source=@WORKTREE_ROOT@/.node_modules,target=/opt/node_modules,type=bind,readonly,z"
    check("node_modules mount", mount in base_config["mounts"], True)
    mount = "source=@CACHE_ROOT@/warden,target=/home/vscode/.cache/warden,type=bind,readonly,z"
    check("warden mount", mount in base_config["mounts"], True)
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        (root / "tools").mkdir()
        for script in ("initialize.py", "config.py"):
            shutil.copy(HERE / script, root / "tools" / script)
        for script in ("warden-chrome.py", "claude-config.py", "extensions.py", "bundle-db.py"):
            (root / "tools" / script).write_text(FAKE)
        base = root / "src" / "master"
        folder = base / NAME
        (folder / "odoo" / "node_modules" / "eslint").mkdir(parents=True)
        (base / "shared.js").write_text("shared\n")
        os.link(base / "shared.js", folder / "odoo" / "node_modules" / "eslint" / "index.js")
        (folder / "enterprise").mkdir()
        (folder / "odoo" / "addons" / "web" / "tooling").mkdir(parents=True)
        (folder / "odoo" / "addons" / "web" / "tooling" / "_package.json").write_bytes(PACKAGE)

        res = initialize(root, folder)
        check("exit code", (res.returncode, res.stderr), (0, ""))
        calls = [
            json.loads(line)
            for script in ("warden-chrome.py", "claude-config.py", "extensions.py", "bundle-db.py")
            for line in (root / "tools" / f"{script}.calls").read_text().splitlines()
        ]
        expected = [[], [NAME, "--bundle"], [NAME, str(folder)], [NAME]]
        check("the name, one argument", calls, expected)
        check("no shell", (root / "shell-ran").exists(), False)
        check("the folder of the agent, for its mount", (folder / ".agent").is_dir(), True)
        check("mount source", (root / "src" / ".node_modules").is_dir(), True)
        for repo in ("odoo", "enterprise"):
            path = folder / repo / "node_modules"
            check(f"{repo} link", path.is_symlink() and os.readlink(path), TARGET)
        check("hard link gone", os.stat(base / "shared.js").st_nlink, 1)
        (root / "tools" / "warden-chrome.py").write_text("raise SystemExit(1)\n")
        check("again, with no browser", initialize(root, folder).returncode, 0)
        check("odoo link kept", os.readlink(folder / "odoo" / "node_modules"), TARGET)
        check_npm(root, folder, check)

        (folder / ".agent").rmdir()
        (folder / ".agent").symlink_to(root / "other")
        initialize(root, folder)
        check("a link in its place is not mounted", (folder / ".agent").is_symlink(), False)
        (root / "other" / "node_modules").mkdir(parents=True)
        shutil.rmtree(folder / "odoo")
        (folder / "odoo").symlink_to(root / "other")
        initialize(root, folder)
        check("odoo behind a link", (root / "other" / "node_modules").is_symlink(), False)
    for line in failures:
        print(line)
    print("all as expected" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
