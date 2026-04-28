"""Fully delete a bundle locally.

Examples:
 $ python ~/repo/useful-things/scripts/delete_bundle.py master-bundle-name-ngram
 $ python ~/repo/useful-things/scripts/delete_bundle.py master-bundle-name-ngram --also-remote
"""

from rich import print
from rich.tree import Tree

import argparse
import glob

from command_runner import ignore_error
from commands import (
    clean_bundle_name,
    get_remote_dev_repo,
    get_repo_folder,
    get_repos,
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
)
from utils import UtilsRunner


def delete_bundle(
    runner: UtilsRunner,
    *,
    bundle_name: str,
    force: bool = False,
    also_remote: bool = False,
):
    def handle_repo(runner: UtilsRunner, repo: str):
        runner = runner.with_params(cwd=get_repo_folder(repo))
        wt_bundle_repo_folder = get_worktree_bundle_repo_folder(bundle_name, repo)
        runner.run(
            ["git", "worktree", "remove", wt_bundle_repo_folder, *(["--force"] if force else [])],
            handle_exceptions={
                f"fatal: '{wt_bundle_repo_folder}' is not a working tree": ignore_error,
            },
        )
        runner.delete_branch_and_remote_ref(
            repo=repo,
            bundle_name=bundle_name,
            handle_exceptions={f"error: branch '{bundle_name}' not found": ignore_error},
        )
        if also_remote:
            runner.run(
                ["git", "push", get_remote_dev_repo(repo), "--delete", bundle_name],
                handle_exceptions={
                    f"error: unable to delete '{bundle_name}': remote ref does not exist": ignore_error,
                },
            )

    runner.parallel_run(Tree("Repositories"), get_repos(), handle_repo)
    runner.run(["rm", "-rf", get_worktree_bundle_folder(bundle_name)])

    def handle_file(runner: UtilsRunner, file: str):
        if not file.startswith(f"/home/seb/.local/share/Odoo/filestore/{bundle_name}"):
            print(f"[red]Skipping {file} as it doesn't match the expected pattern[/red]")
            return
        runner.run(["rm", "-rf", file])
        last_part = file.split("/")[-1]
        runner.run(
            ["dropdb", last_part],
            handle_exceptions={
                f'ERROR:  database "{last_part}" does not exist': ignore_error,
                f'dropdb: error: database removal failed: ERROR:  database "{last_part}" does not exist': ignore_error,
            },
        )

    runner.parallel_run(
        Tree("Files"),
        glob.glob(f"/home/seb/.local/share/Odoo/filestore/{bundle_name}*"),
        handle_file,
    )
    print("[green]Done[/green]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="Name of the bundle to delete", type=str)
    parser.add_argument(
        "--also-remote", help="Whether to also delete the remote bundle", action="store_true"
    )
    args = parser.parse_args()
    delete_bundle(
        runner=UtilsRunner(),
        bundle_name=clean_bundle_name(args.name),
        also_remote=args.also_remote,
    )
