"""Write each base repo's objects in one pack, once a month: see README.md.

git maintenance (incremental strategy) never merges its big packs, and a commit in a worktree
touches their mtime, so an object fetched twice stays in two packs: on 2026-10-12 the 30 GB of
packs of the odoo repo held 15.5 million objects, 10.3 million of them distinct, and one repack
brought them to 12.9 GB in 28 minutes. `git repack -a -d -k` keeps every object, reachable or
not, and is no gc: it prunes no worktree.
"""

import glob
import os
import shutil
import subprocess

from commands import get_repo_folder, get_repos

MARGIN = 10 * 2**30


def get_packs_size(folder):
    return sum(os.path.getsize(path) for path in glob.glob(f"{folder}/.git/objects/pack/*.pack"))


def main():
    for repo in get_repos():
        folder = get_repo_folder(repo)
        if not os.path.isdir(f"{folder}/.git"):
            continue
        packs = get_packs_size(folder)
        free = shutil.disk_usage(folder).free
        if free < packs + MARGIN:
            print(f"{repo}: skipped, {free / 2**30:.0f} GB free, {packs / 2**30:.0f} GB of packs")
            continue
        res = subprocess.run(
            ["git", "repack", "-a", "-d", "-k", "--write-midx", "-q"],
            check=False,
            cwd=folder,
        )
        after = get_packs_size(folder)
        sizes = f"{packs / 2**30:.1f} -> {after / 2**30:.1f} GB of packs"
        print(f"{repo}: exit {res.returncode}, {sizes}")


if __name__ == "__main__":
    main()
