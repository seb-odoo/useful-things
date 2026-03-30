"""Fully delete a bundle locally."""

from rich import print
from rich.tree import Tree

import argparse
import glob

from command_runner import ignore_error, live_task_executor, PrintParams, Runner
from commands import (
    clean_bundle_name,
    get_remote_dev_repo,
    get_remote_ref,
    get_repos,
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
)

runner = Runner()

ROOT = "/home/seb/repo"

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the bundle to delete", type=str)
parser.add_argument("--also-remote", help="Whether to also delete the remote bundle", action="store_true")
args = parser.parse_args()
bundle_name = clean_bundle_name(args.name)
also_remote = args.also_remote


def handle_repo(repo, print_params: PrintParams):
    repo_folder = f"{ROOT}/{repo}"
    print_params = print_params.tree_add(repo)
    wt_bundle_repo_folder = get_worktree_bundle_repo_folder(bundle_name, repo)
    runner.run(
        ["git", "worktree", "remove", wt_bundle_repo_folder],
        cwd=repo_folder,
        print_params=print_params,
        handle_exceptions={
            f"fatal: '{wt_bundle_repo_folder}' is not a working tree": ignore_error,
        },
    )
    runner.run(
        ["git", "update-ref", "-d", get_remote_ref(bundle_name, repo)],
        cwd=repo_folder,
        print_params=print_params,
    )
    runner.run(
        ["git", "branch", "-D", bundle_name],
        cwd=repo_folder,
        print_params=print_params,
        handle_exceptions={f"error: branch '{bundle_name}' not found": ignore_error},
    )
    if also_remote:
        runner.run(
            ["git", "push", get_remote_dev_repo(repo), "--delete", bundle_name],
            cwd=repo_folder,
            handle_exceptions={
                f"error: unable to delete '{bundle_name}': remote ref does not exist": ignore_error,
            },
            print_params=print_params,
        )


with live_task_executor(Tree("Repositories")) as submit_task:
    for repo in get_repos():
        submit_task(handle_repo, repo)
runner.run(["rm", "-rf", get_worktree_bundle_folder(bundle_name)])


def handle_file(file, print_params: PrintParams):
    print_params = print_params.tree_add(file)
    if not file.startswith(f"/home/seb/.local/share/Odoo/filestore/{bundle_name}"):
        print(f"[red]Skipping {file} as it doesn't match the expected pattern[/red]")
        return
    runner.run(["rm", "-rf", file], print_params=print_params)
    last_part = file.split("/")[-1]
    runner.run(
        ["dropdb", last_part],
        print_params=print_params,
        handle_exceptions={
            f'ERROR:  database "{last_part}" does not exist': ignore_error,
            f'dropdb: error: database removal failed: ERROR:  database "{last_part}" does not exist': ignore_error,
        },
    )


with live_task_executor(Tree("Files")) as submit_task:
    for matching_file in glob.glob(f"/home/seb/.local/share/Odoo/filestore/{bundle_name}*"):
        submit_task(handle_file, matching_file)
print("[green]Done[/green]")
