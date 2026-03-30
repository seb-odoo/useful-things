"""Create a bundle locally

Example:
 $ python ~/repo/useful-things/scripts/create_bundle.py master bundle-name
Note: -ngram is automatically suffixed depending on config value.
"""

from rich import print
from rich.tree import Tree

import argparse
import os

from commands import (
    get_bundle_name_from_base_and_name,
    get_remote_branch_name,
    get_remote_dev_repo,
    get_repos,
    get_worktree_bundle_repo_folder,
)
from utils import UtilsRunner

runner = UtilsRunner()

parser = argparse.ArgumentParser()
parser.add_argument("base", help="Name of the base to create the bundle for. Eg. master", type=str)
parser.add_argument("name", help="Name of the bundle to create", type=str)
args = parser.parse_args()
bundle_name = get_bundle_name_from_base_and_name(args.base, args.name)


def handle_repo(runner: UtilsRunner, repo):
    runner.git_fetch(repo=repo, dev=False, ref=args.base)
    target_ref = get_remote_branch_name(args.base, repo)
    worktree_bundle_repo_folder = get_worktree_bundle_repo_folder(bundle_name, repo)
    if os.environ.get("PWD").split("/")[-1] == repo:
        runner.add_worktree(
            repo=repo,
            bundle_name=bundle_name,
            make_branch=True,
            target_ref=target_ref,
            on_existing=lambda runner: runner.switch_to_branch(
                repo=repo,
                branch=bundle_name,
                target_ref=target_ref,
            ),
        )
        runner.run(
            ["git", "push", "-u", get_remote_dev_repo(repo), bundle_name],
            cwd=worktree_bundle_repo_folder,
        )
    else:
        runner.add_worktree(
            repo=repo,
            bundle_name=bundle_name,
            make_branch=False,
            target_ref=target_ref,
            on_existing=lambda runner: runner.run(
                ["git", "checkout", target_ref],
                cwd=worktree_bundle_repo_folder,
            ),
        )


runner.prepare_worktree_bundle_folder(bundle_name=bundle_name)
runner.parallel_run(Tree("Repositories"), get_repos(), handle_repo)
print("[green]Done[/green]")
