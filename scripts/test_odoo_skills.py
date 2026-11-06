#!/usr/bin/env python3
"""What odoo_skills.sync copies from the skills folder of odoo master, on a scratch repo:
python3 test_odoo_skills.py"""

import contextlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile

import odoo_skills

REF = "refs/remotes/odoo/master"
GIT = ["git", "-c", "user.name=me", "-c", "user.email=me@localhost", "-c", "commit.gpgsign=false"]


def git(cwd, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        [*GIT, *args], capture_output=True, check=True, cwd=cwd, env=env, text=True
    ).stdout.strip()


def write(repo, files):
    for path, text in files.items():
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)


def commit(repo):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "x", "--allow-empty")
    git(repo, "update-ref", REF, "HEAD")


def sync(repo, target):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        odoo_skills.sync(repo_folder=str(repo), ref=REF, target=target)
    return out.getvalue()


def tree(target):
    return sorted(
        str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()
    )


def main():
    os.environ.pop("GIT_TEST_ASSUME_DIFFERENT_OWNER", None)
    failures, total = [], 0

    def check(name, found, expected):
        nonlocal total
        total += 1
        if found != expected:
            failures.append(f"{name}: {found!r}, expected {expected!r}")

    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        repo = root / "odoo"
        repo.mkdir()
        git(repo, "init", "-q")
        target = root / "claude" / "skills"
        check("no ref: one line", sync(repo, target).count("\n"), 1)
        check("no ref: nothing written", target.exists(), False)

        write(
            repo,
            {
                "skills/odoo-git/SKILL.md": "git\n",
                "skills/odoo-git/run.py": "print(1)\n",
                "skills/odoo-guidelines/guidelines/orm.md": "orm\n",
                "skills/odoo-new/SKILL.md": "new\n",
                "skills/README.md": "readme\n",
            },
        )
        commit(repo)
        sha = git(repo, "rev-parse", REF)
        out = sync(repo, target)
        check(
            "copied with the tree",
            tree(target),
            [".odoo-master-sha", "odoo-git/SKILL.md", "odoo-guidelines/guidelines/orm.md"],
        )
        check("stamp", (target / ".odoo-master-sha").read_text().strip(), sha)
        check("new skill named", "New Odoo skill odoo-new in odoo master" in out, True)
        check("add it to SKILLS", "add it to SKILLS in odoo_skills.py" in out, True)
        check("non md file named", "skills/odoo-git/run.py is not a .md file" in out, True)
        check("readme not named", "README" in out, False)
        check("summary line", out.splitlines()[-1], f"Odoo skills: 2 skills from odoo/master {sha[:10]}")
        check("second run is a no-op", sync(repo, target), "")

        (repo / "skills/odoo-git/SKILL.md").write_text("git 2\n")
        (repo / "skills/odoo-guidelines/guidelines/orm.md").unlink()
        write(repo, {"skills/odoo-review/SKILL.md": "review\n"})
        commit(repo)
        out = sync(repo, target)
        check(
            "new commit replaces the copy",
            tree(target),
            [".odoo-master-sha", "odoo-git/SKILL.md", "odoo-review/SKILL.md"],
        )
        check("content updated", (target / "odoo-git/SKILL.md").read_text(), "git 2\n")
        check("no leftover folder", sorted(p.name for p in target.parent.iterdir()), ["skills"])
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
