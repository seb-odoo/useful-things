"""Various utility methods."""

from command_runner import Runner
from commands import (
    get_remote_branch_name,
    get_remote_dev_branch_name,
    get_remote_dev_repo,
    get_remote_repo,
    get_repo_folder,
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
    get_worktree_container_folder,
)


class UtilsRunner(Runner):
    """Class containing various utility methods."""

    def add_worktree(
        self,
        *,
        repo,
        bundle_name,
        make_branch,
        target_ref,
        track=False,
        on_existing=None,
    ):
        target_folder = get_worktree_bundle_repo_folder(bundle_name, repo)
        handle_exceptions = None
        if on_existing:
            handle_exceptions = {
                f"fatal: '{target_folder}' already exists": on_existing,
                f"fatal: '{bundle_name}' is already used by worktree at '{target_folder}'": on_existing,
            }
        self.run(
            ["git", "worktree", "add"]
            + (["-B", bundle_name] if make_branch else [])
            + [target_folder, target_ref]
            + (["--track"] if track else []),
            cwd=get_repo_folder(repo),
            handle_exceptions=handle_exceptions,
        )

    def git_fetch(self, *, repo, dev, ref):
        remote = get_remote_dev_repo(repo) if dev else get_remote_repo(repo)
        self.run(["git", "fetch", remote, ref], cwd=get_repo_folder(repo))

    def prepare_worktree_bundle_folder(self, *, bundle_name):
        worktree_bundle_folder = get_worktree_bundle_folder(bundle_name)
        self.run(["mkdir", "-p", worktree_bundle_folder])
        self.run(
            [
                "ln",
                "-sfn",
                "/home/seb/repo/useful-things/odools.toml",
                f"{worktree_bundle_folder}/odools.toml",
            ],
        )
        self.run(
            [
                "ln",
                "-sfn",
                f"{get_worktree_container_folder()}/.vscode",
                f"{worktree_bundle_folder}/.vscode",
            ],
        )

    def switch_to_branch(self, *, repo, branch, target_ref: str = None):
        cwd = get_worktree_bundle_repo_folder(branch, repo)
        if not target_ref:
            target_ref = get_remote_dev_branch_name(branch, repo)
        self.run(["git", "switch", "-C", branch, target_ref], cwd=cwd)
