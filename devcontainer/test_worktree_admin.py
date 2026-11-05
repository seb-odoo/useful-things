#!/usr/bin/env python3
"""What worktree-admin.py does to the worktrees of a bundle, on git repos in a temporary folder:
python3 test_worktree_admin.py

Where `unshare -Urm` works, it also binds them as base.jsonc does and tries the writes of a bundle.
"""

import os
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "worktree-admin.py"
GIT = ["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
# Set by the ops container, where git with no config read trusts no repo.
ENV.pop("GIT_TEST_ASSUME_DIFFERENT_OWNER", None)
BASE = HERE / "base.jsonc"
MOUNT = re.compile(r'"source=([^,"]+),target=([^,"]+),type=bind((?:,\w+)*)"')
# A bundle after the mounts: what it writes in the base .git once the host renamed its config.
WRITES = """
cd "$1"
for name in config hooks/x info/x worktrees/$3/commondir worktrees/new x \\
    worktrees/$2/x objects/x refs/x logs/x rr-cache/x packed/x; do
    (: >> "$name") 2>/dev/null && echo "wrote $name" && [ "${name##*/}" = x ] && rm "$name"
done
cd "$4"
git commit -q --allow-empty -m bundle
echo x > stashed && git stash -q --include-untracked 2>&1 && git stash pop -q 2>&1 >/dev/null && rm stashed
git rev-parse -q --verify refs/stash && echo "a stash left"
git checkout -q -b tmp 2>&1 && git checkout -q - 2>&1 && git update-ref -d refs/heads/tmp 2>&1
git rebase -q --force-rebase HEAD~1 2>&1
git branch -q gone 2>&1 && git update-ref -d refs/heads/gone 2>&1
true
"""

failures = []


def is_folder(path):
    return path.is_dir() and not path.is_symlink()


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


def get_mounts(root, repo, bundle):
    """The mounts of base.jsonc on the base .git of one repo, in the order podman makes them."""
    mounts = []
    for source, target, options in MOUNT.findall(BASE.read_text()):
        if source.startswith(f"@REPO_ROOT@/{repo}/.git"):
            names = [
                path.replace("@REPO_ROOT@", str(root)).replace(
                    "${localWorkspaceFolderBasename}", bundle
                )
                for path in (source, target)
            ]
            mounts.append((*names, "readonly" in options.split(",")))
    return sorted(mounts, key=lambda mount: mount[1].count("/"))


def check_bind(root, worktree, other):
    """The mounts of a bundle in a user namespace, a rename of the config on the host, then what
    the bundle can write: the same as a running container, as each is a mount namespace."""
    if subprocess.run(["unshare", "-Urm", "true"], capture_output=True, check=False).returncode:
        print("no user namespace here: the binds are not tried")
        return
    git_dir = root / "odoo" / ".git"
    with tempfile.TemporaryDirectory() as side:
        lines = [f'mount --bind "{root}" "{side}"']
        for source, target, readonly in get_mounts(root, "odoo", worktree.parent.name):
            lines.append(f'mount --bind "{side}{source.removeprefix(str(root))}" "{target}"')
            if readonly:
                lines.append(f'mount -o remount,bind,ro "{target}"')
        script = "set -e\n" + "\n".join(lines) + "\nset +e\necho ready\nread go\n" + WRITES
        args = [git_dir, worktree.parent.name, other, worktree]
        with subprocess.Popen(
            ["unshare", "-Urm", "bash", "-c", script, "_", *map(str, args)],
            env={**ENV, **{f"GIT_{key}_EMAIL": "me@localhost" for key in ("AUTHOR", "COMMITTER")}},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ) as inside:
            check("bound", inside.stdout.readline(), "ready\n")
            git(root / "odoo", "config", "host.renamed", "true")
            out, _ = inside.communicate("go\n", timeout=60)
    wrote = [line.removeprefix("wrote ") for line in out.splitlines() if line.startswith("wrote ")]
    check(
        "bound as in a bundle, after a rename of the config on the host, what it writes",
        wrote,
        [f"worktrees/{worktree.parent.name}/x", "objects/x", "refs/x", "logs/x", "rr-cache/x", "packed/x"],
    )
    check("and what git says there", [line for line in out.splitlines() if line not in
                                      [f"wrote {name}" for name in wrote]], [])
    check("the config the host wrote", git(root / "odoo", "config", "host.renamed"), "true")


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
        git(odoo, "branch", "packed")
        git(odoo, "pack-refs", "--all")
        packed = (odoo / ".git" / "packed-refs").read_text()
        (odoo / ".git" / "rr-cache").symlink_to(root / "elsewhere")
        res = run(two, root=repos)
        check("a folder of the base .git that is a link", res.returncode, 1)
        check("says so", "is a link" in res.stderr, True)
        (odoo / ".git" / "rr-cache").unlink()

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
        for name in ("objects", "refs", "logs", "rr-cache", "packed"):
            check(f"{name} a folder", is_folder(odoo / ".git" / name), True)
        check("packed-refs a link", os.readlink(odoo / ".git" / "packed-refs"), "packed/packed-refs")
        check("to what it held", (odoo / ".git" / "packed" / "packed-refs").read_text(), packed)
        git(odoo, "branch", "-D", "packed")
        check("a ref deleted on the host", git(odoo, "branch", "--list", "packed"), "")
        check("keeps the link", (odoo / ".git" / "packed-refs").is_symlink(), True)
        (odoo / ".git" / "packed" / "packed-refs").unlink()
        (odoo / ".git" / "packed" / "packed-refs").symlink_to(root / "elsewhere")
        res = run(one, root=repos)
        check("a link there", (res.returncode, "removed" in res.stderr), (0, True))
        check("is gone", os.path.lexists(odoo / ".git" / "packed" / "packed-refs"), False)
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

        check_bind(repos, one / "odoo", two.name)
    for line in failures:
        print(line)
    print("all as expected" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
