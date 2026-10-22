#!/usr/bin/env python3
"""A new bundle without design-themes and documentation, and add_repo.py bringing one back, on
scratch repos, a create killed in its tooling, and which bundles get their own postgres folders:
python3 test_optional_repos.py
"""

import os
import pathlib
import subprocess
import sys
import tempfile

from config import MASTER_ONLY_REPOS, folder_by_repo, remote_by_repo

GIT = ["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]
HERE = pathlib.Path(__file__).resolve().parent
PREPARE = "import sys, utils; utils.UtilsRunner().prepare_worktree_bundle_folder(bundle_name=sys.argv[1])"


def git(cwd, *args):
    return subprocess.run(
        [*GIT, *args], capture_output=True, check=True, cwd=cwd, text=True
    ).stdout.strip()


def prepare(root):
    """One repo per bundle repo, its master on a bare remote; odoo with a no-op enable.sh."""
    for repo in folder_by_repo:
        folder = root / "repo" / repo
        folder.mkdir(parents=True)
        git(folder, "init", "--quiet", "--initial-branch=master")
        if repo == "odoo":
            tooling = folder / "addons" / "web" / "tooling"
            tooling.mkdir(parents=True)
            (tooling / "enable.sh").write_text("exit 0\n")
            git(folder, "add", ".")
        git(folder, "commit", "--quiet", "--allow-empty", "-m", "base")
        for remote in {remote_by_repo[repo], "origin" if repo in MASTER_ONLY_REPOS else "odoo-dev"}:
            bare = root / "remotes" / f"{repo}-{remote}.git"
            git(root, "init", "--quiet", "--bare", str(bare))
            git(folder, "remote", "add", remote, str(bare))
        git(folder, "push", "--quiet", remote_by_repo[repo], "master")
    return dict(
        os.environ,
        BUNDLE_SUFFIX="--me",
        HOST_BOX="",
        OWL_DEV_REMOTE="origin",
        REPO_ROOT=str(root / "repo"),
        SHARE_ROOT=str(root / "share"),
        WORKTREE_ROOT=str(root / "src"),
    )


def run(env, script, *args):
    res = subprocess.run(
        [sys.executable, script, *args],
        capture_output=True,
        check=False,
        cwd=HERE,
        env=env,
        text=True,
    )
    return res.returncode, res.stdout + res.stderr


def repos_of(root, bundle):
    folder = root / "src" / "master" / bundle
    return sorted(path.name for path in folder.iterdir() if (path / ".git").exists())


def head_of(root, bundle, repo):
    folder = root / "src" / "master" / bundle / repo
    return git(folder, "rev-parse", "--abbrev-ref", "HEAD"), git(folder, "rev-parse", "HEAD")


def check(root):
    env = prepare(root)
    base = git(root / "repo" / "documentation", "rev-parse", "master")
    others = sorted(set(folder_by_repo) - {"design-themes", "documentation"})
    checks = []
    for name, branch_repo in (("a", "odoo"), ("b", "documentation"), ("c", "odoo")):
        code, out = run(env, "create_bundle.py", "master", name, "--no-push", "--no-open",
                        "--branch-repo", branch_repo)
        if code:
            return [f"create_bundle.py master {name}: {out}"], 1
    checks.append(("a new bundle has neither design-themes nor documentation",
                   repos_of(root, "master-a--me") == others))
    checks.append(("a bundle with its branch in documentation has it",
                   repos_of(root, "master-b--me") == sorted([*others, "documentation"])
                   and head_of(root, "master-b--me", "documentation")[0] == "master-b--me"))
    code, out = run(env, "add_repo.py", "master-a--me", "design-themes")
    checks.append(("a repo with no branch comes on its base branch",
                   (code, head_of(root, "master-a--me", "design-themes"))
                   == (0, ("HEAD", git(root / "repo" / "design-themes", "rev-parse", "master")))))
    git(root / "repo" / "documentation", "branch", "master-a--me")
    code, out = run(env, "add_repo.py", "master-a--me", "documentation")
    checks.append(("a repo with a local branch comes on it",
                   (code, head_of(root, "master-a--me", "documentation"))
                   == (0, ("master-a--me", base))))
    documentation = root / "repo" / "documentation"
    git(documentation, "push", "--quiet", "odoo-dev", "master:refs/heads/master-c--me")
    code, out = run(env, "add_repo.py", "master-c--me", "documentation")
    upstream = git(root / "src" / "master" / "master-c--me" / "documentation",
                   "rev-parse", "--abbrev-ref", "@{upstream}") if not code else out
    checks.append(("a repo with a branch on the dev remote comes on it, tracking it",
                   (code, head_of(root, "master-c--me", "documentation")[0], upstream)
                   == (0, "master-c--me", "odoo-dev/master-c--me")))
    code, out = run(env, "add_repo.py", "master-a--me", "documentation")
    checks.append(("a repo already there is left as it is",
                   (code, out.strip()) == (0, "documentation is already in master-a--me")))
    code, out = run(env, "add_repo.py", "master-z--me", "documentation")
    checks.append(
        ("no bundle, no worktree", (code, out.strip()) == (1, "No bundle master-z--me here")),
    )
    odoo = root / "repo" / "odoo"
    made = root / "src" / "master" / "master-f--me" / ".made"
    ends = []
    for text in ("kill -TERM $PPID\n", "exit 0\n"):
        (odoo / "addons" / "web" / "tooling" / "enable.sh").write_text(text)
        git(odoo, "commit", "--quiet", "--all", "-m", "enable.sh")
        git(odoo, "push", "--quiet", "odoo", "master")
        code, out = run(env, "create_bundle.py", "master", "f", "--no-push", "--no-open",
                        "--branch-repo", "odoo")
        ends.append((code, made.exists()))
    checks.append(("a create killed in enable.sh leaves no .made, and the same create finishes it",
                   ends == [(-15, False), (0, True)]))
    for bundle, folder in (("master-d--me", None), ("master-e--me", root / "src" / "master" / "master-e--me")):
        if folder:
            folder.mkdir()
        run(env, "-c", PREPARE, bundle)
    db = root / "share" / "bundle-db"
    checks.append(("a new bundle gets its own postgres folders", (db / "master-a--me" / "pg" / "16").is_dir()))
    checks.append(("a bare one too, before its worktrees", (db / "master-d--me" / "pg" / "16").is_dir()))
    checks.append(("a bundle made before gets none", not (db / "master-e--me").exists()))
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
