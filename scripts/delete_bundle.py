"""Fully delete a bundle locally."""

from concurrent.futures import ThreadPoolExecutor
from rich import print
from rich.live import Live
from rich.tree import Tree

import argparse
import glob

from command_runner import ignore_error, PrintParams, Runner
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


def handle_repo(repo, live, tree):
    repo_folder = f"{ROOT}/{repo}"
    current_tree = tree.add(repo)
    live.refresh()
    repo_wt_root_folder = f"{wt_root_folder}/{repo}"
    runner.run(
        ["git", "worktree", "remove", repo_wt_root_folder],
        cwd=repo_folder,
        print_params=PrintParams(live, current_tree),
        handle_exceptions={
            f"fatal: '{repo_wt_root_folder}' is not a working tree": ignore_error,
        },
    )
    runner.run(
        ["git", "update-ref", "-d", f"refs/remotes/{remote_dev_by_repo[repo]}/{bundle_name}"],
        cwd=repo_folder,
        print_params=PrintParams(live, current_tree),
    )
    runner.run(
        ["git", "branch", "-D", bundle_name],
        cwd=repo_folder,
        print_params=PrintParams(live, current_tree),
        handle_exceptions={f"error: branch '{bundle_name}' not found": ignore_error},
    )


tree = Tree("Repositories")
with Live(tree, auto_refresh=False) as live:
    futures = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        for repo in repos:
            futures.append(executor.submit(handle_repo, repo, live, tree))
    for future in futures:
        future.result()
    live.refresh()

runner.run(["rm", "-rf", wt_root_folder])

path_pattern = f"/home/seb/.local/share/Odoo/filestore/{bundle_name}*"
matching_files = glob.glob(path_pattern)

tree = Tree("Files")
with Live(tree, auto_refresh=False) as live:
    for matching_file in matching_files:
        current_tree = tree.add(matching_file)
        live.refresh()
        if not matching_file.startswith(f"/home/seb/.local/share/Odoo/filestore/{bundle_name}"):
            print(f"[red]Skipping {matching_file} as it doesn't match the expected pattern[/red]")
            continue
        runner.run(["rm", "-rf", matching_file], print_params=PrintParams(live, current_tree))
        last_part = matching_file.split("/")[-1]
        runner.run(
            ["dropdb", last_part],
            print_params=PrintParams(live, current_tree),
            handle_exceptions={
                f'ERROR:  database "{last_part}" does not exist': ignore_error,
                f'dropdb: error: database removal failed: ERROR:  database "{last_part}" does not exist': ignore_error,
            },
        )
    live.refresh()

print("[green]Done[/green]")
