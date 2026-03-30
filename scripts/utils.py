"""Various utility methods."""

from config import folder_by_repo, remote_by_repo, remote_dev_by_repo

from command_runner import PrintParams, Runner
from commands import (
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
    get_worktree_container_folder,
)


def add_worktree(
    *,
    runner: Runner,
    repo,
    bundle_name,
    make_branch,
    target_ref,
    track=False,
    on_existing=None,
    print_params: PrintParams,
):
    repo_folder = folder_by_repo[repo]
    target_folder = get_worktree_bundle_repo_folder(bundle_name, repo)
    handle_exceptions = None
    if on_existing:
        handle_exceptions = {
            f"fatal: '{target_folder}' already exists": on_existing,
            f"fatal: '{bundle_name}' is already used by worktree at '{target_folder}'": on_existing,
        }
    runner.run(
        ["git", "worktree", "add"]
        + (["-B", bundle_name] if make_branch else [])
        + [target_folder, target_ref]
        + (["--track"] if track else []),
        print_params=print_params,
        cwd=repo_folder,
        handle_exceptions=handle_exceptions,
    )


def git_fetch(*, runner: Runner, repo, dev, ref, print_params: PrintParams):
    repo_folder = folder_by_repo[repo]
    remote = remote_dev_by_repo[repo] if dev else remote_by_repo[repo]
    runner.run(["git", "fetch", remote, ref], print_params=print_params, cwd=repo_folder)


def prepare_worktree_bundle_folder(*, runner: Runner, bundle_name):
    worktree_bundle_folder = get_worktree_bundle_folder(bundle_name)
    runner.run(["mkdir", "-p", worktree_bundle_folder])
    runner.run(
        [
            "ln",
            "-sfn",
            "/home/seb/repo/useful-things/odools.toml",
            f"{worktree_bundle_folder}/odools.toml",
        ],
    )
    runner.run(
        [
            "ln",
            "-sfn",
            f"{get_worktree_container_folder()}/.vscode",
            f"{worktree_bundle_folder}/.vscode",
        ],
    )


def switch_to_branch(*, runner, cwd, branch, target_ref, print_params: PrintParams):
    runner.run(["git", "switch", "-C", branch, target_ref], print_params=print_params, cwd=cwd)
