"""The bundles on a PR of an author outside the odoo org, see devcontainer/README.md ("Outside
PRs").

    python3 outside.py start BUNDLE FOLDER     # initializeCommand, on the host

Such a bundle reads the memory of the bundles from a copy of its own, and what its agent writes
there comes to Seb as a handoff proposal, `BUNDLE@memory` in hok: a note that a stranger's text
steered reaches no other bundle before his yes. The mark is the file `outside` of the Claude
folder of the bundle, which no container mounts: the host sets it when it reads a PR of the bundle
by such an author, at the fetch of that PR and at each start of the container, and never takes it
off.
"""

import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from config import CLAUDE_CONFIG_CONTAINER, folder_by_repo

HOME = pathlib.Path.home()
SHARED = HOME / ".claude" / "projects" / "-workspace" / "memory"
INBOX = HOME / ".claude" / "handoff"
OWN = pathlib.Path(CLAUDE_CONFIG_CONTAINER)
ORG = "odoo"
INSIDE = {"MEMBER", "OWNER"}
# Only Odoo pushes there: the forward-ports of fw-bot are inside whatever its membership.
DEV_OWNERS = {"odoo", "odoo-dev"}
MARK = "outside"
NOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.md")
PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
MAX_NOTES = 200
MAX_SIZE = 1024 * 1024
QUIET = 60
ROOT = "dev-memory"
SUFFIX = "@memory"
WHY = "WHY"


def gh(*args):
    found = shutil.which("gh") or str(HOME / ".local" / "bin" / "gh")
    res = subprocess.run([found, *args], capture_output=True, check=False, text=True, timeout=20)
    if res.returncode and not res.stdout.strip():
        raise OSError(f"gh failed: {res.stderr.strip()[-300:]}")
    return json.loads(res.stdout)


def get_pr(node):
    """A PR as the mark keeps it, from the GraphQL or the REST shape."""
    if "html_url" in node:
        head = ((node.get("head") or {}).get("repo") or {}).get("owner") or {}
        return {
            "url": node["html_url"],
            "author": (node.get("user") or {}).get("login"),
            "association": node.get("author_association"),
            "head": head.get("login"),
        }
    return {
        "url": node["url"],
        "author": (node.get("author") or {}).get("login"),
        "association": node.get("authorAssociation"),
        "head": (node.get("headRepositoryOwner") or {}).get("login"),
    }


def is_outside(pr):
    return pr["association"] not in INSIDE and pr["head"] not in DEV_OWNERS


def read_branch_prs(branch, repos, states="OPEN"):
    """The PRs whose head is this branch, of any fork, in these repos of the org."""
    queries = [
        f'r{i}: repository(owner: "{ORG}", name: "{repo}") {{ pullRequests(headRefName:'
        f" {json.dumps(branch)}, states: [{states}], first: 20) {{ nodes {{ url"
        " authorAssociation author { login } headRepositoryOwner { login } } } }"
        for i, repo in enumerate(repos)
    ]
    data = gh("api", "graphql", "-f", f"query={{ {' '.join(queries)} }}").get("data") or {}
    return [
        get_pr(node) for found in data.values() if found for node in found["pullRequests"]["nodes"]
    ]


def read_mark(bundle):
    try:
        return json.loads((OWN / bundle / MARK).read_text())
    except (OSError, ValueError):
        return None


def mark(bundle, prs):
    """Mark the bundle when one of these PRs is by an author outside the org."""
    pr = next((pr for pr in prs if is_outside(pr)), None)
    if pr is None or read_mark(bundle):
        return read_mark(bundle)
    (OWN / bundle).mkdir(parents=True, exist_ok=True)
    found = {**pr, "at": time.strftime("%Y-%m-%d %H:%M")}
    (OWN / bundle / MARK).write_text(json.dumps(found) + "\n")
    return found


def check_fetch(bundle, name):
    """Mark a bundle fetched from a PR, or from a branch of a fork, by an author outside the
    org. Unknown counts as outside: Seb asked for this PR, and GitHub answered for it already."""
    if pr_match := PR_URL.match(name):
        try:
            prs = [get_pr(gh("api", f"repos/{pr_match[1]}/pulls/{pr_match[2]}"))]
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            prs = [{"url": pr_match[0], "author": None, "association": None, "head": None}]
        return mark(bundle, prs)
    owner, _, branch = name.rpartition(":")
    if not owner or owner in DEV_OWNERS:
        return read_mark(bundle)
    try:
        prs = read_branch_prs(branch, list(folder_by_repo), "OPEN, CLOSED, MERGED")
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        prs = []
    prs = [pr for pr in prs if pr["head"] == owner]
    return mark(bundle, prs or [{"url": name, "author": owner, "association": None, "head": owner}])


def check_start(bundle, folder):
    """Mark the bundle when an open PR of an author outside the org has its branch as head, in a
    repo it has. GitHub out of reach leaves the mark as it was."""
    repos = [repo for repo in folder_by_repo if (pathlib.Path(folder) / repo).exists()]
    try:
        return mark(bundle, read_branch_prs(bundle, repos or ["odoo"]))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(
            f"{bundle}: the PRs of its branch are not read, its mark stays: {error}",
            file=sys.stderr,
        )
        return read_mark(bundle)


def open_folder(path):
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def read_notes(fd):
    """{name: (bytes, mtime)} of the notes of an open folder, no link followed."""
    notes = {}
    for name in sorted(os.listdir(fd)):
        if not NOTE.fullmatch(name) or len(notes) >= MAX_NOTES:
            continue
        try:
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except OSError:
            continue
        try:
            info = os.fstat(child)
            if stat.S_ISREG(info.st_mode) and info.st_size <= MAX_SIZE:
                notes[name] = (os.read(child, MAX_SIZE + 1), info.st_mtime)
        finally:
            os.close(child)
    return notes


def write_note(fd, name, data):
    """Write a note by rename, which replaces a link the bundle left there and follows none."""
    fresh = f".{name}.new"
    try:
        os.unlink(fresh, dir_fd=fd)
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(fresh, flags, 0o644, dir_fd=fd), "wb") as file:
        file.write(data)
    os.rename(fresh, name, src_dir_fd=fd, dst_dir_fd=fd)


def merge(shared, base, mine):
    """The note as the bundle wrote it, over what the shared memory got since the bundle took
    it, both sides kept where they change the same lines."""
    if shared is None or shared == base:
        return mine
    with tempfile.TemporaryDirectory() as folder:
        paths = []
        for name, data in (("shared", shared), ("base", base or b""), ("mine", mine)):
            path = pathlib.Path(folder, name)
            path.write_bytes(data)
            paths.append(str(path))
        res = subprocess.run(
            ["git", "merge-file", "-p", "--union", *paths],
            capture_output=True,
            check=False,
            cwd=folder,
        )
    return res.stdout if 0 <= res.returncode < 128 else mine


class Folders:
    """The copy of a bundle, what it got there last (base), and the shared memory, open."""

    def __init__(self, bundle):
        self.bundle = bundle

    def __enter__(self):
        own = OWN / self.bundle
        self.fds = []
        try:
            for path in (own / "memory", own / "memory-base", SHARED):
                self.fds.append(open_folder(path))
        except OSError:
            self.__exit__()
            raise
        return self.fds

    def __exit__(self, *exc):
        for fd in self.fds:
            os.close(fd)


def collect(bundle, quiet=0):
    """Propose to Seb the notes the bundle changed in its copy since it got them, merged into the
    shared memory, once the last change is `quiet` seconds old and no proposal of its notes
    waits. Returns the names proposed."""
    found = read_mark(bundle)
    inbox = INBOX / f"{bundle}{SUFFIX}"
    if found is None or os.path.lexists(inbox / WHY):
        return []
    with Folders(bundle) as (copy_fd, base_fd, shared_fd):
        mine, base, shared = map(read_notes, (copy_fd, base_fd, shared_fd))
        changed = {}
        for name, (data, mtime) in mine.items():
            if data == base.get(name, (None,))[0]:
                continue
            if data == shared.get(name, (None,))[0]:
                write_note(base_fd, name, data)
                continue
            changed[name] = data, mtime
        if not changed or time.time() - max(mtime for _, mtime in changed.values()) < quiet:
            return []
        (inbox / ROOT).mkdir(parents=True, exist_ok=True)
        for name, (data, _) in changed.items():
            old = base.get(name, (None,))[0]
            (inbox / ROOT / name).write_bytes(merge(shared.get(name, (None,))[0], old, data))
        who = found.get("author") or "an author GitHub did not name"
        (inbox / WHY).write_text(
            f"memory notes of {bundle}, a bundle on {found['url']} by {who}, outside odoo:"
            " no other bundle reads them before your yes\n"
        )
        for name, (data, _) in changed.items():
            write_note(base_fd, name, data)
    return sorted(changed)


def refresh(bundle):
    """Give the copy what the shared memory has, except the notes the bundle changed and the
    ones that wait for Seb's answer."""
    inbox = INBOX / f"{bundle}{SUFFIX}"
    waiting = set()
    if os.path.lexists(inbox / WHY):
        waiting = {path.name for path in (inbox / ROOT).glob("*")}
    with Folders(bundle) as (copy_fd, base_fd, shared_fd):
        mine, base, shared = map(read_notes, (copy_fd, base_fd, shared_fd))
        for name, (data, _) in shared.items():
            if name in waiting:
                continue
            if name not in mine or mine[name][0] == base.get(name, (None,))[0]:
                write_note(copy_fd, name, data)
                write_note(base_fd, name, data)
        for name in base.keys() - shared.keys() - waiting:
            if mine.get(name, (None,))[0] == base[name][0]:
                os.unlink(name, dir_fd=copy_fd)
            os.unlink(name, dir_fd=base_fd)


def prepare(bundle):
    """What base.jsonc mounts as the memory of the bundle: the shared one through a link, or for
    a marked bundle its copy, after its changes went to Seb."""
    own = OWN / bundle
    copy = own / "memory"
    own.mkdir(parents=True, exist_ok=True)
    if read_mark(bundle) is None:
        if copy.is_dir() and not copy.is_symlink():
            shutil.rmtree(copy)
        if not copy.is_symlink() or os.readlink(copy) != str(SHARED):
            if os.path.lexists(copy):
                copy.unlink()
            copy.symlink_to(SHARED)
        return
    if copy.is_symlink():
        copy.unlink()
    copy.mkdir(exist_ok=True)
    (own / "memory-base").mkdir(exist_ok=True)
    collect(bundle)
    refresh(bundle)


def collect_all():
    """collect() for each marked bundle, from the daemon of bundle-ctl."""
    for found in sorted(OWN.glob(f"*/{MARK}")):
        try:
            collect(found.parent.name, QUIET)
        except OSError as error:
            print(f"{found.parent.name}: its notes are not collected: {error}", flush=True)


def main():
    if len(sys.argv) != 4 or sys.argv[1] != "start":
        sys.exit(__doc__.strip())
    bundle, folder = sys.argv[2:]
    check_start(bundle, folder)
    prepare(bundle)


if __name__ == "__main__":
    main()
