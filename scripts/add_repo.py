"""Add to a bundle the worktree of a repo it was made without

Example:
 $ python ~/repo/useful-things/scripts/add_repo.py master-x--seb documentation
The worktree is on the bundle branch when the repo has one, here or on the dev remote, else on the
base branch.
"""

import argparse
import os

from commands import (
    MADE,
    get_base_for_repo,
    get_base_from_bundle_name,
    get_remote_branch_name,
    get_remote_dev_branch_name,
    get_repo_folder,
    get_repos,
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
)
from rich import print
from rich.tree import Tree
from utils import UtilsRunner

parser = argparse.ArgumentParser()
parser.add_argument("bundle", help="Name of the bundle. Eg. master-x--seb")
parser.add_argument("repo", choices=sorted(get_repos()))
args = parser.parse_args()
bundle_name = args.bundle

if not os.path.exists(f"{get_worktree_bundle_folder(bundle_name)}/{MADE}"):
    print(f"No bundle [red]{bundle_name}[/red] here")
    raise SystemExit(1)
if os.path.exists(get_worktree_bundle_repo_folder(bundle_name, args.repo)):
    print(f"{args.repo} is already in {bundle_name}")
    raise SystemExit(0)


def handle_repo(runner: UtilsRunner, repo):
    local = runner.run(
        ["git", "for-each-ref", "--format=%(refname)", f"refs/heads/{bundle_name}"],
        cwd=get_repo_folder(repo),
    )
    if local.stdout.strip():
        runner.add_worktree(
            repo=repo,
            bundle_name=bundle_name,
            make_branch=False,
            target_ref=bundle_name,
        )
    elif runner.remote_has_branch(repo=repo, branch=bundle_name):
        runner.git_fetch(repo=repo, dev=True, ref=bundle_name)
        runner.add_worktree(
            repo=repo,
            bundle_name=bundle_name,
            make_branch=True,
            target_ref=get_remote_dev_branch_name(bundle_name, repo),
            track=True,
        )
    else:
        base = get_base_for_repo(get_base_from_bundle_name(bundle_name), repo)
        runner.git_fetch(repo=repo, dev=False, ref=base)
        runner.add_worktree(
            repo=repo,
            bundle_name=bundle_name,
            make_branch=False,
            target_ref=get_remote_branch_name(base, repo),
        )


UtilsRunner().parallel_run(Tree("Repositories"), [args.repo], handle_repo)
print("[green]Done[/green]")
