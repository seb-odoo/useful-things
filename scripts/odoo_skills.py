"""Copy the official agent skills of odoo master into the skills folder bundles see as
/workspace/.claude/skills: only the `.md` files of the skills named in SKILLS.
Called at the end of fetch_all.py and create_bundle.py.

Every bundle writes the objects/ and refs/ of the odoo .git, and every bundle agent loads these
skills (security check 11-26, B201). So the commit is the one the last fetch of the host gave
master (upstream.keep_heads), never the ref, and each object read from it is checked against its
name: git itself returns a forged object file, or a `git replace`, as is."""

import hashlib
import os
import pathlib
import re
import shutil
import subprocess

import upstream
from commands import GIT, get_repo_folder, get_worktree_container_folder

SKILLS = ("odoo-git", "odoo-guidelines", "odoo-review", "odoo-security", "odoo-web-guidelines")
STAMP = ".odoo-master-checked"
FILE_MODES = ("100644", "100755")
TREE_MODE = "40000"
MAX_OBJECT = 4 << 20
OBJECT_NAME = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class ForgedObject(Exception):
    pass


class Objects:
    """The objects of a repo, through one `git cat-file --batch`, each one hashed again."""

    def __init__(self, repo_folder):
        self.repo_folder = repo_folder
        self.process = None

    def __enter__(self):
        object_format = subprocess.run(
            [*GIT, "rev-parse", "--show-object-format"],
            capture_output=True,
            check=True,
            cwd=self.repo_folder,
            text=True,
        ).stdout.strip()
        self.hash = {"sha1": hashlib.sha1, "sha256": hashlib.sha256}[object_format]
        self.process = subprocess.Popen(
            [*GIT, "cat-file", "--batch"],
            cwd=self.repo_folder,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, *exc):
        self.process.stdin.close()
        self.process.kill()
        self.process.wait()

    def read(self, sha, kind):
        self.process.stdin.write(f"{sha}\n".encode())
        self.process.stdin.flush()
        header = self.process.stdout.readline().decode(errors="replace").split()
        if len(header) != 3 or header[1] != kind or not header[2].isdigit():
            raise ForgedObject(sha)
        size = int(header[2])
        if size > MAX_OBJECT:
            raise ForgedObject(sha)
        data = self.process.stdout.read(size)
        self.process.stdout.read(1)
        if self.hash(f"{kind} {size}\0".encode() + data).hexdigest() != sha:
            raise ForgedObject(sha)
        return data

    def tree(self, sha):
        """[(mode, name, sha)] of a tree, in the order git keeps them."""
        data, size, entries, start = self.read(sha, "tree"), len(sha) // 2, [], 0
        while start < len(data):
            space = data.index(b" ", start)
            end = data.index(b"\0", space)
            entries.append(
                (
                    data[start:space].decode(),
                    data[space + 1 : end].decode(errors="replace"),
                    data[end + 1 : end + 1 + size].hex(),
                )
            )
            start = end + 1 + size
        return entries

    def files(self, sha, prefix):
        """[(path, mode, sha)] of every file under a tree, as `git ls-tree -r` lists them."""
        found = []
        for mode, name, child in self.tree(sha):
            if mode == TREE_MODE:
                found.extend(self.files(child, f"{prefix}{name}/"))
            else:
                found.append((f"{prefix}{name}", mode, child))
        return found


def read_skills(objects, sha):
    """{path: bytes} of the `.md` files of the skills of SKILLS at commit SHA."""
    commit = objects.read(sha, "commit").decode(errors="replace")
    root = commit.split("\n", 1)[0].removeprefix("tree ")
    skills = [
        child for mode, name, child in objects.tree(root) if (mode, name) == (TREE_MODE, "skills")
    ]
    found = objects.files(skills[0], "skills/") if skills else []
    files, new_skills = {}, []
    for path, mode, blob in sorted(found):
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
        files[path] = objects.read(blob, "blob")
    return files


def sync(repo_folder=None, target=None):
    repo_folder = repo_folder or get_repo_folder("odoo")
    target = pathlib.Path(target or f"{get_worktree_container_folder()}/.claude/skills")
    sha = upstream.read_heads("odoo").get("master")
    if not isinstance(sha, str) or not OBJECT_NAME.fullmatch(sha):
        print("Odoo skills: no commit of odoo master kept from a fetch of the host, not synced")
        return
    try:
        if (target / STAMP).read_text().strip() == sha:
            return
    except OSError:
        pass
    try:
        with Objects(repo_folder) as objects:
            files = read_skills(objects, sha)
    except ForgedObject as error:
        print(
            f"Odoo skills: object {error} of odoo master {sha[:10]} is missing or does not match "
            "its name, not synced"
        )
        return
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Odoo skills: odoo master {sha[:10]} not read ({error}), not synced")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    new = target.parent / f".{target.name}.new"
    old = target.parent / f".{target.name}.old"
    for leftover in (new, old):
        shutil.rmtree(leftover, ignore_errors=True)
    new.mkdir()
    for path, data in files.items():
        destination = new / path.removeprefix("skills/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (new / STAMP).write_text(sha + "\n")
    if target.exists():
        os.rename(target, old)
    os.rename(new, target)
    shutil.rmtree(old, ignore_errors=True)
    count = len({path.split("/")[1] for path in files})
    print(f"Odoo skills: {count} skills from odoo/master {sha[:10]}")
