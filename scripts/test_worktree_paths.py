#!/usr/bin/env python3
"""The host scripts run git, rm and ln only in a real worktree of a bundle, on scratch repos:
python3 test_worktree_paths.py

A bundle writes its own entry in `.git/worktrees` and its bundle folder. Through either, a path
can name a repo of the bundle's choice out of the box of the host git, whose hook or
`core.fsmonitor` would then run (security check 11-03, C4).
"""

import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
GIT = ["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]
HOOK = "#!/bin/sh\ntouch {marker}\n"
RELEASE = (
    "import sys, utils; runner = utils.UtilsRunner().with_params(cwd=sys.argv[1]);"
    " print('\\nreleased', utils.UtilsRunner()._release_branch_from_worktrees(runner, sys.argv[2]))"
)
UNSAVED = (
    "import sys, delete_bundle, utils;"
    " print(delete_bundle.get_unsaved_work(utils.UtilsRunner(), sys.argv[1]))"
)
CHECK = "import sys, commands; print([commands.is_bundle_worktree(path) for path in sys.argv[1:]])"


def git(cwd, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    env.pop("GIT_TEST_ASSUME_DIFFERENT_OWNER", None)
    return subprocess.run(
        [*GIT, *args], capture_output=True, check=True, cwd=cwd, env=env, text=True
    ).stdout.strip()


def run(env, code, *args):
    res = subprocess.run(
        [sys.executable, "-c", code, *map(str, args)],
        capture_output=True,
        check=False,
        cwd=HERE,
        env=env,
        text=True,
    )
    return res.stdout.strip() or res.stderr.strip()


def make_evil(folder, marker):
    """A repo of the bundle's choice: its hook and its fsmonitor touch marker."""
    git(folder.parent, "init", "-q", "-b", "B", str(folder))
    git(folder, "commit", "-q", "--allow-empty", "-m", "evil")
    (folder / ".git" / "hooks" / "post-checkout").write_text(HOOK.format(marker=marker))
    (folder / ".git" / "hooks" / "post-checkout").chmod(0o755)
    (folder / "fsmonitor").write_text(HOOK.format(marker=marker))
    (folder / "fsmonitor").chmod(0o755)
    git(folder, "config", "core.fsmonitor", str(folder / "fsmonitor"))


def main():
    failures = []
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name).resolve()
        base = root / "repo" / "odoo"
        git(root, "init", "-q", "-b", "master", str(base))
        git(base, "commit", "-q", "--allow-empty", "-m", "base")
        bundle, other = root / "src" / "master" / "master-a--me", root / "src" / "master" / "master-b--me"
        bundle.mkdir(parents=True)
        other.mkdir(parents=True)
        git(base, "worktree", "add", "-q", "-b", bundle.name, str(bundle / "odoo"))
        marker = root / "ran"
        evil = root / "share" / "evil"
        evil.parent.mkdir()
        make_evil(evil, marker)
        entry = base / ".git" / "worktrees" / "odoo"
        (entry / "gitdir").write_text(f"{evil}/.git\n")
        (entry / "HEAD").write_text("ref: refs/heads/master-b--me\n")
        git(base, "branch", "master-b--me")
        env = {
            **os.environ,
            "BUNDLE_SUFFIX": "--me",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "REPO_ROOT": str(root / "repo"),
            "SHARE_ROOT": str(root / "share"),
            "WORKTREE_ROOT": str(root / "src"),
            "XDG_CONFIG_HOME": str(root / "config"),
        }
        env.pop("GIT_TEST_ASSUME_DIFFERENT_OWNER", None)
        found = run(env, RELEASE, base, "master-b--me").rpartition("released ")[2]
        checks = [("an entry naming a repo out of the roots, released", found, "[]")]
        checks.append(("its hook and fsmonitor", marker.exists(), False))
        checks.append(("its branch", git(evil, "branch", "--show-current"), "B"))
        (other / "odoo").symlink_to(evil)
        run(env, UNSAVED, other.name)
        checks.append(("a repo folder that is a link, read for unsaved work", marker.exists(), False))
        found = run(env, CHECK, bundle / "odoo", other / "odoo", evil, bundle)
        checks.append(("which paths are worktrees of a bundle", found, "[True, False, False, False]"))
    for what, got, expected in checks:
        if got != expected:
            failures.append(f"{what}: got {got!r}, expected {expected!r}")
    for line in failures:
        print(line)
    print(f"{len(checks) - len(failures)}/{len(checks)} cases as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
