#!/usr/bin/env python3
"""Name the admin folder of each worktree of a bundle after the bundle.

    python3 worktree-admin.py <bundle folder> [<repo> <base repo folder>]

git keeps what a worktree has of its own (HEAD, index, its lock) in `.git/worktrees/<id>` of the
base repo, `<id>` being the name of the worktree folder with a number (`odoo`, `odoo1`, ...). A
bundle has `.git/worktrees` read-only, as the `commondir` of another bundle's worktree points its
git at a config of the writer's choice, and its own folder there writable (base.jsonc): the
container config all bundles share can only name it `${localWorkspaceFolderBasename}`. So this
moves the folder of each worktree of the bundle to `.git/worktrees/<bundle>` and points the `.git`
file of the worktree at it. A folder moves only when its `gitdir` file, which no bundle writes,
names that worktree: the `.git` file is the bundle's own. A repo the bundle has no worktree of
gets an empty folder, locked against `git worktree prune`, as a missing mount source stops the
start. With a repo, only that one, its base repo at the given folder.

Run by initialize.py before each start, by add_worktree for a new worktree, by the stand-in of a
remote agent.
"""

import os
import pathlib
import shutil
import stat
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import config  # noqa: E402

REPOS = (
    "odoo",
    "enterprise",
    "design-themes",
    "documentation",
    "upgrade",
    "upgrade-util",
    "owl",
    "sfu",
)
EMPTY = "no worktree of the bundle: the source of its mount (devcontainer/worktree-admin.py)\n"


def read(path, dir_fd=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    with os.fdopen(fd) as file:
        return file.read(4096)


def is_folder(path):
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def clear(path):
    if is_folder(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.unlink(path)


def adopt(worktree, worktrees, name):
    try:
        fd = os.open(worktree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return
    try:
        try:
            text = read(".git", dir_fd=fd)
        except OSError:
            return
        old = pathlib.Path(text.removeprefix("gitdir:").strip())
        if not text.startswith("gitdir:") or not old.is_absolute() or old.name == name:
            return
        if os.path.realpath(old.parent) != os.path.realpath(worktrees) or old.name in ("", ".."):
            return
        source = worktrees / old.name
        mine = f"{os.path.realpath(worktree.parent)}/{worktree.name}/.git"
        if not is_folder(source) or read(source / "gitdir").strip() != mine:
            print(f"{worktree}: {source} is not its folder, left as it is", file=sys.stderr)
            return
        target = worktrees / name
        if is_folder(target):
            # A running container binds this folder: its content changes, never the folder.
            for entry in os.listdir(target):
                clear(target / entry)
            for entry in os.listdir(source):
                os.rename(source / entry, target / entry)
            os.rmdir(source)
        else:
            clear(target)
            os.rename(source, target)
        try:
            os.unlink(".git.new", dir_fd=fd)
        except FileNotFoundError:
            pass
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(os.open(".git.new", flags, 0o644, dir_fd=fd), "w") as file:
            file.write(f"gitdir: {old.parent / name}\n")
        os.replace(".git.new", ".git", src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        os.close(fd)


def own(folder, repo, base):
    worktrees = base / ".git" / "worktrees"
    if not (base / ".git").is_dir():
        return
    worktrees.mkdir(exist_ok=True)
    try:
        adopt(folder / repo, worktrees, folder.name)
    except OSError as error:
        print(f"{folder / repo}: its folder in {worktrees} stays: {error}", file=sys.stderr)
    target = worktrees / folder.name
    if not is_folder(target):
        clear(target)
        target.mkdir()
        (target / "locked").write_text(EMPTY)


def main():
    if len(sys.argv) not in (2, 4):
        sys.exit(__doc__.strip())
    folder = pathlib.Path(sys.argv[1])
    if folder.name in ("", ".", ".."):
        sys.exit(f"{folder} names no bundle")
    if len(sys.argv) == 4:
        own(folder, sys.argv[2], pathlib.Path(sys.argv[3]))
        return
    root = pathlib.Path(config.load()["REPO_ROOT"])
    for repo in REPOS:
        own(folder, repo, root / repo)


if __name__ == "__main__":
    main()
