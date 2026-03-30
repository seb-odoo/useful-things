"""Create a bundle locally."""

from rich import print
from rich.tree import Tree

import argparse
import os

from command_runner import live_task_executor, PrintParams, Runner
from commands import (
    get_bundle_name_from_base_and_name,
    get_remote_branch_name,
    get_remote_dev_repo,
    get_repos,
    get_worktree_bundle_repo_folder,
)
from utils import (
    add_worktree,
    git_fetch,
    prepare_worktree_bundle_folder,
    switch_to_branch,
)

runner = Runner()

parser = argparse.ArgumentParser()
parser.add_argument("base", help="Name of the base to create the bundle for. Eg. master", type=str)
parser.add_argument("name", help="Name of the bundle to create", type=str)
args = parser.parse_args()
bundle_name = get_bundle_name_from_base_and_name(args.base, args.name)


def handle_repo(repo, print_params: PrintParams):
    print_params = print_params.tree_add(repo)
    git_fetch(
        runner=runner,
        repo=repo,
        dev=False,
        ref=args.base,
        print_params=print_params,
    )
    target_ref = get_remote_branch_name(args.base, repo)
    worktree_bundle_repo_folder = get_worktree_bundle_repo_folder(bundle_name, repo)
    if os.environ.get("PWD").split("/")[-1] == repo:

        def handle_existing_worktree(print_params):
            switch_to_branch(
                runner=runner,
                cwd=worktree_bundle_repo_folder,
                branch=bundle_name,
                target_ref=target_ref,
                print_params=print_params,
            )

        add_worktree(
            runner=runner,
            repo=repo,
            bundle_name=bundle_name,
            make_branch=True,
            target_ref=target_ref,
            on_existing=handle_existing_worktree,
            print_params=print_params,
        )
        runner.run(
            ["git", "push", "-u", get_remote_dev_repo(repo), bundle_name],
            cwd=worktree_bundle_repo_folder,
            print_params=print_params,
        )
    else:
        add_worktree(
            runner=runner,
            repo=repo,
            bundle_name=bundle_name,
            make_branch=False,
            target_ref=target_ref,
            on_existing=lambda print_params: runner.run(
                ["git", "checkout", target_ref],
                print_params=print_params,
                cwd=worktree_bundle_repo_folder,
            ),
            print_params=print_params,
        )


prepare_worktree_bundle_folder(runner=runner, bundle_name=bundle_name)
with live_task_executor(Tree("Repositories")) as submit_task:
    for repo in get_repos():
        submit_task(handle_repo, repo)
print("[green]Done[/green]")
