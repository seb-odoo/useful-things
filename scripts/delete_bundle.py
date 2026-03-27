"""Fully delete a bundle locally."""

from rich import print
from rich.tree import Tree

import argparse
import glob

from command_runner import ignore_error, live_task_executor, PrintParams, Runner
from utils import get_base_from_bundle_name

runner = Runner()

ROOT = "/home/seb/repo"
repos = ["design-themes", "documentation", "enterprise", "odoo", "upgrade-util", "upgrade"]
remote_dev_by_repo = {
    "design-themes": "odoo-dev",
    "documentation": "odoo-dev",
    "enterprise": "odoo-dev",
    "odoo": "odoo-dev",
    "upgrade-util": "origin",
    "upgrade": "odoo",
}

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the branch to delete", type=str)
args = parser.parse_args()
bundle_name = args.name.replace("odoo-dev:", "")
base = get_base_from_bundle_name(bundle_name)
wt_root_folder = f"/home/seb/src/odoo/{base}/{bundle_name}"


def handle_repo(repo, print_params: PrintParams):
    repo_folder = f"{ROOT}/{repo}"
    print_params = print_params.tree_add(repo)
    repo_wt_root_folder = f"{wt_root_folder}/{repo}"
    runner.run(
        ["git", "worktree", "remove", repo_wt_root_folder],
        cwd=repo_folder,
        print_params=print_params,
        handle_exceptions={
            f"fatal: '{repo_wt_root_folder}' is not a working tree": ignore_error,
        },
    )
    runner.run(
        ["git", "update-ref", "-d", f"refs/remotes/{remote_dev_by_repo[repo]}/{bundle_name}"],
        cwd=repo_folder,
        print_params=print_params,
    )
    runner.run(
        ["git", "branch", "-D", bundle_name],
        cwd=repo_folder,
        print_params=print_params,
        handle_exceptions={f"error: branch '{bundle_name}' not found": ignore_error},
    )


with live_task_executor(Tree("Repositories")) as submit_task:
    for repo in repos:
        submit_task(handle_repo, repo)
runner.run(["rm", "-rf", wt_root_folder])


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
