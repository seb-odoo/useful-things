#!/usr/bin/env python3
"""The files enable.sh writes in a bundle are no work to keep, a renamed one too, while a file of
Seb at the same place or in a folder enable.sh wrote still is, on scratch repos with no .gitignore:
python3 test_tooling_files.py
"""

import json
import pathlib
import subprocess
import sys
import tempfile

from config import folder_by_repo
from test_optional_repos import git, prepare, run

ENABLE = """cd "$(dirname "$0")/../../.."
for name in {names}; do
    echo '{{}}' > "$name"
    echo '{{}}' > ../enterprise/"$name"
done
mkdir -p node_modules ../enterprise/node_modules addons/new
"""
NAMES = ".eslint-ignore eslint.config.cjs jsconfig.json package.json package-lock.json"
FINISH = (
    "import sys, utils; "
    "utils.UtilsRunner().finish_worktree_bundle_folder(bundle_name=sys.argv[1], open_window=False)"
)
UNSAVED = (
    "import json, pathlib, sys, delete_bundle, utils; "
    "unsaved = delete_bundle.get_unsaved_work(utils.UtilsRunner(), sys.argv[1]); "
    "pathlib.Path(sys.argv[2]).write_text(json.dumps(unsaved))"
)


def check(root):
    env = prepare(root)
    odoo = root / "repo" / "odoo"
    checks = []

    def push_enable(names):
        (odoo / "addons" / "web" / "tooling" / "enable.sh").write_text(ENABLE.format(names=names))
        git(odoo, "commit", "--quiet", "--all", "-m", "enable.sh")
        git(odoo, "push", "--quiet", "odoo", "master")

    def create(name):
        code, out = run(env, "create_bundle.py", "master", name, "--no-push", "--no-open",
                        "--branch-repo", "odoo")
        if code:
            raise RuntimeError(f"create_bundle.py master {name}: {out}")

    def unsaved(bundle):
        code, out = run(env, "-c", UNSAVED, bundle, str(root / "unsaved.json"))
        return json.loads((root / "unsaved.json").read_text()) if not code else out

    def worktree(bundle, repo):
        return root / "src" / "master" / bundle / repo

    push_enable(NAMES)
    create("a")
    checks.append(("a new bundle holds no work", unsaved("master-a--me") == {}))
    (worktree("master-a--me", "enterprise") / "notes.txt").write_text("mine\n")
    (worktree("master-a--me", "odoo") / "addons" / "mine.py").write_text("")
    run(env, "-c", FINISH, "master-a--me")
    checks.append(("a file of Seb at the root or in a folder enable.sh wrote stays work",
                   unsaved("master-a--me")
                   == {"enterprise": {"uncommitted files": ["?? notes.txt"]},
                       "odoo": {"uncommitted files": ["?? addons/mine.py"]}}))
    (worktree("master-a--me", "enterprise") / "notes.txt").unlink()
    (worktree("master-a--me", "odoo") / "addons" / "mine.py").unlink()
    cleaned = []
    for made in ("another bundle made", "its tooling run again"):
        for repo in folder_by_repo:
            (root / "repo" / repo / ".git" / "info" / "exclude").write_text("")
        before = unsaved("master-a--me")
        if made == "another bundle made":
            create("b")
        else:
            run(env, "-c", FINISH, "master-a--me")
        cleaned.append((sorted(before), unsaved("master-a--me")))
    checks.append(("a bundle made before is clean once another one is made, or once refetched",
                   cleaned == [(["enterprise", "odoo"], {})] * 2))
    push_enable(NAMES.replace("eslint.config.cjs", "eslint.config.mjs") + " '*'")
    create("c")
    checks.append(("a file enable.sh renames is no work either, a name git reads as a pattern is",
                   unsaved("master-c--me")
                   == {repo: {"uncommitted files": ["?? *"]} for repo in ("odoo", "enterprise")}))
    for repo in ("odoo", "enterprise"):
        (worktree("master-c--me", repo) / "*").unlink()
    removed = []
    for repo in ("odoo", "enterprise"):
        git(root / "repo" / repo, "worktree", "unlock", str(worktree("master-c--me", repo)))
        removed.append(
            subprocess.run(
                ["git", "worktree", "remove", str(worktree("master-c--me", repo))],
                capture_output=True,
                check=False,
                cwd=root / "repo" / repo,
            ).returncode
        )
    checks.append(("git worktree remove takes the worktrees with no --force", removed == [0, 0]))
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
