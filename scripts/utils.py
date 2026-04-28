"""Various utility methods."""

from collections import defaultdict
from collections.abc import Iterable
import re

from command_runner import Runner
from commands import (
    BUNDLE_SUFFIX,
    get_base_from_bundle_name,
    get_remote_dev_branch_name,
    get_remote_dev_repo,
    get_remote_dev_ref,
    get_remote_ref,
    get_remote_repo,
    get_repo_folder,
    get_worktree_base_repo,
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
    get_worktree_container_folder,
)


class RemoteRefManager:
    gone_repos_by_ref = defaultdict(set)
    valid_repos_by_ref = defaultdict(set)

    def add_gone(self, repo, ref):
        self.gone_repos_by_ref[ref].add(repo)

    def add_valid(self, repo, ref):
        self.valid_repos_by_ref[ref].add(repo)

    @property
    def safe_to_delete_refs(self):
        return {ref for ref in self._fully_gone_refs if not ref.endswith(BUNDLE_SUFFIX)}

    @property
    def refs_to_prompt_for_deletion(self):
        return {ref for ref in self._fully_gone_refs if ref.endswith(BUNDLE_SUFFIX)}

    @property
    def repo_ref_to_clean(self):
        return {
            (repo, ref)
            for ref, repos in self.gone_repos_by_ref.items()
            for repo in repos
            if ref in self.valid_repos_by_ref and repo not in self.valid_repos_by_ref[ref]
        }

    @property
    def _fully_gone_refs(self):
        return {
            ref for ref in self.gone_repos_by_ref.keys() if not self.valid_repos_by_ref.get(ref)
        }


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

    def delete_branch_and_remote_ref(self, *, repo, bundle_name, handle_exceptions=None):
        runner = self.with_params(cwd=get_repo_folder(repo))
        runner.run(["git", "update-ref", "-d", get_remote_ref(bundle_name, repo)])
        runner.run(["git", "update-ref", "-d", get_remote_dev_ref(bundle_name, repo)])
        runner.run(["git", "branch", "-D", bundle_name], handle_exceptions=handle_exceptions)

    def finish_worktree_bundle_folder(self, *, bundle_name):
        runner = self.with_params(cwd=get_worktree_bundle_folder(bundle_name))
        for repo in ("odoo", "enterprise"):
            base_node_folder = f"{get_worktree_base_repo(get_base_from_bundle_name(bundle_name), repo)}/node_modules"
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

    def git_fetch(self, *, repo, dev, ref=None, remote_ref_manager: RemoteRefManager = None):
        if ref is not None and not ref:
            return
        if ref is None:
            ref = []
        remote = get_remote_dev_repo(repo) if dev else get_remote_repo(repo)
        ref = ref if isinstance(ref, Iterable) and not isinstance(ref, str) else [ref]

        def handle_fetch_exception(runner: Runner, e):
            print(e.stderr)
            match = re.search(r"fatal: couldn't find remote ref\s+([^\s]+)", e.stderr)
            if match:
                gone_ref = match.group(1)
                if remote_ref_manager:
                    remote_ref_manager.add_gone(repo, gone_ref)
                runner.git_fetch(
                    repo=repo,
                    dev=dev,
                    ref=[r for r in ref if r != gone_ref],
                    remote_ref_manager=remote_ref_manager,
                )
                return gone_ref

        self.run(
            ["git", "fetch", remote, *ref, "-p"],
            cwd=get_repo_folder(repo),
            handle_exceptions=handle_fetch_exception,
            on_success=lambda: [
                remote_ref_manager.add_valid(repo, r) if remote_ref_manager else None for r in ref
            ],
        )

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
