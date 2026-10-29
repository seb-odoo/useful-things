"""The upstream of a bundle branch, set on the host before its container starts.

    python3 upstream.py BUNDLE     # initializeCommand, on the host

A container reads the `.git/config` of the base repos read-only (devcontainer/base.jsonc), so the
first plain `git push` of a bundle made by `bctl new` cannot set its upstream there
(`push.autoSetupRemote`): it pushes, then prints two errors. Each repo where BUNDLE is a branch
with no upstream gets it here, on the dev remote that push goes to.
"""

import os
import subprocess
import sys

from commands import BUNDLE_NAME, get_remote_dev_repo, get_repo_folder, get_repos

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


def main():
    if len(sys.argv) != 2 or not BUNDLE_NAME.fullmatch(sys.argv[1]):
        sys.exit(__doc__.strip())
    bundle = sys.argv[1]
    for repo in get_repos():
        if os.path.isdir(get_repo_folder(repo)) and set_upstream(repo, bundle):
            print(f"{repo}: {bundle} tracks {get_remote_dev_repo(repo)}/{bundle}")


if __name__ == "__main__":
    main()
