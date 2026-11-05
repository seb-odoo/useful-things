#!/usr/bin/env python3
"""Make the transcripts folder of a bundle, and what it reads of the others.

    python3 transcripts.py <bundle> <bundle folder>

Each bundle writes a transcripts folder of its own, SHARE_ROOT/claude-transcripts/<bundle>, which
base.jsonc mounts as its ~/.claude/projects/-workspace, where Claude keeps the sessions of
/workspace. Before, every bundle wrote the one folder of all of them: a bundle read what Seb
typed in the others, and added turns to their sessions, which the host then resumed in their
bundle (security check 11-03, B7). A bundle reads the folders of the others at ~/bundle-transcripts
and the folder they shared before at ~/bundle-transcripts-before, both read-only, for a bundle to
learn from another. A bundle on a PR of an author outside odoo (scripts/outside.py) reads none:
both are links of its Claude folder, to those folders, or to an empty one.

The session of the bundle (.agent/session) comes from the old shared folder when its own folder
has none: the transcript, and the regular files of its folder, copied, with no link followed.

Run by initialize.py before each start, after outside.py, and by the stand-in of a remote agent.
"""

import os
import pathlib
import re
import shutil
import stat
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import config  # noqa: E402

BUNDLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def read_session(folder):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(pathlib.Path(folder) / ".agent" / "session", flags)
    except OSError:
        return None
    with os.fdopen(fd) as file:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        session = file.read(128).strip()
    return session if SESSION_ID.fullmatch(session) else None


def copy_file(source, target):
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return
    with os.fdopen(fd, "rb") as file:
        if stat.S_ISREG(os.fstat(fd).st_mode) and not os.path.lexists(target):
            with open(target, "xb") as copy:
                shutil.copyfileobj(file, copy)


def copy_tree(source, target):
    """The folders and regular files of SOURCE in TARGET, where the bundle may have left a link."""
    if not os.path.lexists(target):
        target.mkdir()
    if target.is_symlink() or not target.is_dir():
        return
    with os.scandir(source) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                copy_tree(pathlib.Path(entry.path), target / entry.name)
            elif entry.is_file(follow_symlinks=False):
                copy_file(entry.path, target / entry.name)


def link(path, target):
    if path.is_symlink() and os.readlink(path) == str(target):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif os.path.lexists(path):
        path.unlink()
    path.symlink_to(target)


def main():
    if len(sys.argv) != 3 or not BUNDLE_NAME.fullmatch(sys.argv[1]):
        sys.exit(__doc__.strip())
    bundle, folder = sys.argv[1:]
    values = config.load()
    root = pathlib.Path(values["SHARE_ROOT"]) / "claude-transcripts"
    old = pathlib.Path(values["HOME"]) / ".claude" / "projects" / "-workspace"
    own = pathlib.Path(values["CACHE_ROOT"]) / "devcontainer" / "claude-config" / bundle
    mine = root / bundle
    mine.mkdir(parents=True, exist_ok=True)
    old.mkdir(parents=True, exist_ok=True)
    session = read_session(folder)
    if session and not os.path.lexists(mine / f"{session}.jsonl"):
        copy_file(old / f"{session}.jsonl", mine / f"{session}.jsonl")
        if (old / session).is_dir() and not (old / session).is_symlink():
            copy_tree(old / session, mine / session)
    own.mkdir(parents=True, exist_ok=True)
    (own / "empty").mkdir(exist_ok=True)
    outside = (own / "outside").is_file()
    link(own / "transcripts", own / "empty" if outside else root)
    link(own / "transcripts-before", own / "empty" if outside else old)


if __name__ == "__main__":
    main()
