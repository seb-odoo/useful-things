"""Various utility methods."""

from collections.abc import Iterable

from command_runner import Runner
from commands import (
    get_base_from_bundle_name,
    get_remote_dev_branch_name,
    get_remote_dev_repo,
    get_remote_dev_ref,
    get_remote_ref,
    get_remote_repo,
    get_repo_folder,
    get_worktree_base_folder,
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

    def delete_remote_ref(self, *, repo, bundle_name):
        self.run(["git", "update-ref", "-d", get_remote_ref(bundle_name, repo)])
        self.run(["git", "update-ref", "-d", get_remote_dev_ref(bundle_name, repo)])

    def finish_worktree_bundle_folder(self, *, bundle_name):
        runner = self.with_params(cwd=get_worktree_bundle_folder(bundle_name))
        wt_base_folder = get_worktree_base_folder(get_base_from_bundle_name(bundle_name))
        for repo in ("odoo", "enterprise"):
            base_node_folder = f"{wt_base_folder}/{repo}/node_modules"
            runner.run(["mkdir", "-p", base_node_folder])
            runner.run(
                [
                    "ln",
                    "-sfn",
                    base_node_folder,
                    f"{get_worktree_bundle_repo_folder(bundle_name, repo)}/node_modules",
                ]
            )
        runner.run(["bash", "./odoo/addons/web/tooling/enable.sh"], input="y\n")
        runner.run(["code", "."])

    def git_fetch(self, *, repo, dev, ref=None):
        if ref is not None and not ref:
            return
        if ref is None:
            ref = []
        remote = get_remote_dev_repo(repo) if dev else get_remote_repo(repo)
        ref = ref if isinstance(ref, Iterable) and not isinstance(ref, str) else [ref]
        self.run(["git", "fetch", remote, *ref, "-p"], cwd=get_repo_folder(repo))

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
