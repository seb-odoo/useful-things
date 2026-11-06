"""The upstream of a bundle branch, set on the host before its container starts.

    python3 upstream.py BUNDLE     # initializeCommand, on the host

A container reads the `.git/config` of the base repos read-only (devcontainer/base.jsonc), so the
first plain `git push` of a bundle made by `bctl new` cannot set its upstream there
(`push.autoSetupRemote`): it pushes, then prints two errors. Each repo where BUNDLE is a branch
with no upstream gets it here, on the dev remote that push goes to.

Also the commit each fetch of the host gave each upstream branch (keep_heads), which the start
of a remote agent checks its base against: every bundle writes the refs of the base `.git`.
"""

import fcntl
import json
import os
import pathlib
import re
import subprocess
import sys

from commands import (
    BUNDLE_NAME,
    get_remote_dev_repo,
    get_remote_repo,
    get_repo_folder,
    get_repos,
)
from config import STATE_ROOT

FETCH_LINE = re.compile(r"([0-9a-f]{40})\t(?:not-for-merge)?\tbranch '([^']+)' of (.+)")
FETCH_TIMEOUT = 120
HEADS = pathlib.Path(STATE_ROOT) / "bundle-ctl" / "upstream"
MAX_FETCH_HEAD = 16 << 20

GIT = ("git", "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null", "-c", "core.pager=cat")


def git(repo, *args):
    return subprocess.run(
        [*GIT, *args],
        capture_output=True,
        check=False,
        cwd=get_repo_folder(repo),
        text=True,
    )


def set_upstream(repo, bundle):
    """Whether the branch got an upstream: a missing branch prints nothing, one with no upstream
    an empty line."""
    res = git(repo, "for-each-ref", "--format=%(upstream)", f"refs/heads/{bundle}")
    if res.returncode or res.stdout != "\n":
        return False
    remote = get_remote_dev_repo(repo)
    return not any(
        git(repo, "config", f"branch.{bundle}.{key}", value).returncode
        for key, value in (("remote", remote), ("merge", f"refs/heads/{bundle}"))
    )


def keep_heads(repo):
    """Keep the commit the fetch of the host that just ended gave each branch of odoo/REPO, from
    the FETCH_HEAD of the base repo: no bundle writes it, where every bundle writes its refs
    (security check 11-05, G102). A fetch goes on when it cannot be kept: the start of a remote
    agent then refuses that base, and the agent starts here."""
    try:
        with open(pathlib.Path(get_repo_folder(repo), ".git", "FETCH_HEAD"), "rb") as file:
            lines = file.read(MAX_FETCH_HEAD).decode(errors="replace").splitlines()
        found = {
            match[2]: match[1]
            for line in lines
            if (match := FETCH_LINE.fullmatch(line))
            and re.search(rf"[:/]odoo/{re.escape(repo)}(\.git)?/?$", match[3])
        }
        if not found:
            return
        HEADS.mkdir(parents=True, exist_ok=True)
        with open(HEADS / ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            heads = {**read_heads(repo), **found}
            temp = HEADS / f".{repo}.json.new"
            temp.write_text(json.dumps(heads, indent=1, sort_keys=True) + "\n")
            os.replace(temp, HEADS / f"{repo}.json")
    except OSError as error:
        print(f"{repo}: the upstream heads of this fetch are not kept: {error}", file=sys.stderr)


def fetch_base(repo, base, env=None):
    """Fetch the branch BASE of odoo/REPO on the host, its commit kept (keep_heads): the start of
    a remote agent sends its base at that commit only, and a bundle's own fetch moves the base
    ref with no record (security check 11-05, G102). One fetch of a repo at a time: two starts at
    once would fail on the ref the other moves. The error of git, None once fetched."""
    remote = get_remote_repo(repo)
    refspec = f"+refs/heads/{base}:refs/remotes/{remote}/{base}"
    try:
        HEADS.mkdir(parents=True, exist_ok=True)
        with open(HEADS / f".{repo}.fetch", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            done = subprocess.run(
                [*GIT, "fetch", "--quiet", "--no-auto-maintenance", remote, refspec],
                capture_output=True,
                check=False,
                cwd=get_repo_folder(repo),
                env=env,
                text=True,
                timeout=FETCH_TIMEOUT,
            )
            if done.returncode:
                return done.stderr.strip()[-300:] or f"exit {done.returncode}"
            keep_heads(repo)
    except subprocess.TimeoutExpired:
        return f"no answer in {FETCH_TIMEOUT} s"
    except OSError as error:
        return str(error)
    return None


def read_heads(repo):
    """{branch: commit} of odoo/REPO as the last fetches of the host gave them (keep_heads)."""
    try:
        heads = json.loads((HEADS / f"{repo}.json").read_text())
    except (OSError, ValueError):
        return {}
    return heads if isinstance(heads, dict) else {}


def main():
    if len(sys.argv) != 2 or not BUNDLE_NAME.fullmatch(sys.argv[1]):
        sys.exit(__doc__.strip())
    bundle = sys.argv[1]
    for repo in get_repos():
        if os.path.isdir(get_repo_folder(repo)) and set_upstream(repo, bundle):
            print(f"{repo}: {bundle} tracks {get_remote_dev_repo(repo)}/{bundle}")


if __name__ == "__main__":
    main()
