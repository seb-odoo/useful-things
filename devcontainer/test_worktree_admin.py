#!/usr/bin/env python3
"""What worktree-admin.py does to the worktrees of a bundle, on git repos in a temporary folder:
python3 test_worktree_admin.py

Where `unshare -Urm` works, it also binds them as base.jsonc does and tries the writes of a bundle.
"""

import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "worktree-admin.py"
GIT = ["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
INSIDE = """set -e
git_dir=$1; own=$2; other=$3; side=$4
mount --bind "$git_dir/worktrees/$own" "$side"
mount --bind "$git_dir/worktrees" "$git_dir/worktrees"
mount -o remount,bind,ro "$git_dir/worktrees"
mount --bind "$side" "$git_dir/worktrees/$own"
set +e
echo x > "$git_dir/worktrees/$own/commondir" && echo own
echo x > "$git_dir/worktrees/$other/commondir" && echo other
mkdir "$git_dir/worktrees/new" && echo new
"""

failures = []


def check(what, got, expected):
    if got != expected:
        failures.append(f"{what}: got {got!r}, expected {expected!r}")


def git(cwd, *args):
    return subprocess.run(
        [*GIT, *args], capture_output=True, check=True, cwd=cwd, env=ENV, text=True
    ).stdout.strip()


def run(*args, root=None):
    env = {**ENV, "REPO_ROOT": str(root)} if root else ENV
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )


def add(base, folder):
    git(base, "worktree", "add", "-q", "-b", folder.name, str(folder / base.name))
    git(base, "worktree", "lock", "--reason", "managed bundle", str(folder / base.name))


def check_bind(git_dir, own, other):
    if subprocess.run(["unshare", "-Urm", "true"], capture_output=True, check=False).returncode:
        print("no user namespace here: the binds are not tried")
        return
    with tempfile.TemporaryDirectory() as side:
        res = subprocess.run(
            ["unshare", "-Urm", "bash", "-c", INSIDE, "_", git_dir, own, other, side],
            capture_output=True,
            check=False,
            text=True,
        )
    check("bound as in a bundle, what it writes", res.stdout.split(), ["own"])


def main():
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name).resolve()
        repos = root / "repo"
        odoo, enterprise = repos / "odoo", repos / "enterprise"
        for base in (odoo, enterprise):
            git(root, "init", "-q", str(base))
            git(base, "commit", "-q", "--allow-empty", "-m", "base")
        src = root / "src" / "master"
        one, two, late = src / "master-one--me", src / "master-two--me", src / "master-late--me"
        for folder in (one, two, late):
            folder.mkdir(parents=True)
        add(odoo, one)
        add(odoo, two)
        add(enterprise, one)
        worktrees = odoo / ".git" / "worktrees"
        check("git names them", sorted(os.listdir(worktrees)), ["odoo", "odoo1"])

        (two / "odoo" / ".git").write_text(f"gitdir: {worktrees / 'odoo'}\n")
        res = run(two, root=repos)
        check(
            "a .git file that names the folder of another bundle",
            sorted(os.listdir(worktrees)),
            ["master-two--me", "odoo", "odoo1"],
        )
        check("says so", "is not its folder" in res.stderr, True)
        (two / "odoo" / ".git").write_text(f"gitdir: {worktrees / 'odoo1'}\n")

        (worktrees / one.name).symlink_to(root / "elsewhere")
        res = run(one, root=repos)
        check("exit code", (res.returncode, res.stderr), (0, ""))
        check(
            "the folder named after the bundle",
            sorted(os.listdir(worktrees)),
            ["master-one--me", "master-two--me", "odoo1"],
        )
        check("a link there replaced", (worktrees / one.name).is_symlink(), False)
        check(
            "its .git file",
            (one / "odoo" / ".git").read_text(),
            f"gitdir: {worktrees / one.name}\n",
        )
        check("git status", git(one / "odoo", "status", "--porcelain"), "")
        git(one / "odoo", "commit", "-q", "--allow-empty", "-m", "work")
        check("a commit", git(odoo, "log", "-1", "--format=%s", one.name), "work")
        check("still locked", (worktrees / one.name / "locked").read_text(), "managed bundle\n")
        check("enterprise too", one.name in os.listdir(enterprise / ".git" / "worktrees"), True)
        for repo in ("design-themes", "sfu"):
            check(f"no {repo} clone, nothing made", (repos / repo).exists(), False)

        run(two, root=repos)
        empty = enterprise / ".git" / "worktrees" / two.name
        check("no worktree, an empty folder", sorted(os.listdir(empty)), ["locked"])
        listed = git(enterprise, "worktree", "list", "--porcelain")
        check("which git lists not", two.name in listed, False)
        git(enterprise, "worktree", "prune")
        check("and keeps", empty.is_dir(), True)
        before = sorted(worktrees.iterdir())
        check(
            "again, nothing moves",
            (run(one, root=repos).returncode, sorted(worktrees.iterdir())),
            (0, before),
        )

        (empty / "commondir").write_text("../../evil\n")
        inode = empty.stat().st_ino
        add(enterprise, two)
        res = run(two, "enterprise", enterprise)
        check("a worktree added while its container runs", (res.returncode, res.stderr), (0, ""))
        check("goes in the folder it binds", empty.stat().st_ino, inode)
        check("what the bundle left there is gone", (empty / "commondir").read_text(), "../..\n")
        check("git status", git(two / "enterprise", "status", "--porcelain"), "")

        (late / "odoo").symlink_to(two / "odoo")
        run(late, root=repos)
        check(
            "odoo a link to the worktree of another",
            (worktrees / late.name).is_dir() and os.listdir(worktrees / late.name),
            ["locked"],
        )
        check(
            "which stays where it is",
            (two / "odoo" / ".git").read_text(),
            f"gitdir: {worktrees / two.name}\n",
        )

        check_bind(odoo / ".git", one.name, two.name)
    for line in failures:
        print(line)
    print("all as expected" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
