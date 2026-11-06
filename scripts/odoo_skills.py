"""Copy the official agent skills of odoo master into the skills folder bundles see as
/workspace/.claude/skills: only the `.md` files of the skills named in SKILLS.
Called at the end of fetch_all.py and create_bundle.py."""

import os
import pathlib
import shutil
import subprocess

from commands import GIT, get_remote_ref, get_repo_folder, get_worktree_container_folder

SKILLS = ("odoo-git", "odoo-guidelines", "odoo-review", "odoo-security", "odoo-web-guidelines")
STAMP = ".odoo-master-sha"
FILE_MODES = ("100644", "100755")


def _git(folder, *args):
    return subprocess.run(
        [*GIT, *args], capture_output=True, check=True, cwd=folder
    ).stdout


def sync(repo_folder=None, ref=None, target=None):
    repo_folder = repo_folder or get_repo_folder("odoo")
    ref = ref or get_remote_ref("master", "odoo")
    target = pathlib.Path(target or f"{get_worktree_container_folder()}/.claude/skills")
    try:
        sha = _git(repo_folder, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        sha = sha.decode().strip()
    except subprocess.CalledProcessError:
        print(f"Odoo skills: no {ref} in {repo_folder}, not synced")
        return
    try:
        if (target / STAMP).read_text().strip() == sha:
            return
    except OSError:
        pass
    listing = _git(repo_folder, "ls-tree", "-r", "-z", sha, "skills/").decode()
    files, new_skills = [], []
    for entry in sorted(filter(None, listing.split("\0"))):
        meta, path = entry.split("\t", 1)
        mode, _kind, _blob = meta.split()
        parts = path.split("/")
        if len(parts) == 2:
            if path != "skills/README.md":
                print(f"Odoo skills: {path} is not in a skill folder, skipped")
            continue
        name = parts[1]
        if name not in SKILLS:
            if name not in new_skills:
                new_skills.append(name)
                print(
                    f"New Odoo skill {name} in odoo master: not synced, "
                    "add it to SKILLS in odoo_skills.py"
                )
            continue
        if mode not in FILE_MODES or not path.endswith(".md"):
            print(f"Odoo skills: {path} is not a .md file, skipped")
            continue
        files.append(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    new = target.parent / f".{target.name}.new"
    old = target.parent / f".{target.name}.old"
    for leftover in (new, old):
        shutil.rmtree(leftover, ignore_errors=True)
    new.mkdir()
    for path in files:
        destination = new / path.removeprefix("skills/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_git(repo_folder, "show", f"{sha}:{path}"))
    (new / STAMP).write_text(sha + "\n")
    if target.exists():
        os.rename(target, old)
    os.rename(new, target)
    shutil.rmtree(old, ignore_errors=True)
    count = len({path.split("/")[1] for path in files})
    print(f"Odoo skills: {count} skills from odoo/master {sha[:10]}")
