"""Various utility methods."""

from collections import defaultdict
from collections.abc import Iterable
import json
import os
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
    get_worktree_base_folder,
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
    get_worktree_container_folder,
)

# Mirrors `workspaceFolder` in useful-things/devcontainer.json (kept in sync by hand: the JSONC file
# isn't trivially parseable and the value is stable for this setup).
WORKSPACE_FOLDER = "/workspace"


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
                f"fatal: '{bundle_name}' is already checked out at '{target_folder}'": on_existing,
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

    def _devcontainer_folder_uri(self, bundle_folder):
        """Build the VS Code folder-URI that opens `bundle_folder` attached to its dev container.

        Reproduces the `dev-container+<hex>` authority VS Code writes itself, so opening it reuses a
        running container (matched by the `devcontainer.local_folder` label) or builds+starts one.
        """
        config_file = f"{bundle_folder}/.devcontainer/devcontainer.json"
        authority = {
            "hostPath": bundle_folder,
            "localDocker": False,
            "settings": {"host": f"unix:///run/user/{os.getuid()}/podman/podman.sock"},
            "configFile": {
                "$mid": 1,
                "fsPath": config_file,
                "external": f"file://{config_file}",
                "path": config_file,
                "scheme": "file",
            },
        }
        hex_authority = json.dumps(authority, separators=(",", ":")).encode().hex()
        return f"vscode-remote://dev-container+{hex_authority}/{WORKSPACE_FOLDER}"

    @staticmethod
    def _node_modules_ready(node_modules):
        """A node_modules is usable once npm install has created its `.bin` directory."""
        return os.path.isdir(os.path.join(node_modules, ".bin"))

    def finish_worktree_bundle_folder(self, *, bundle_name):
        bundle_folder = get_worktree_bundle_folder(bundle_name)
        base_folder = get_worktree_base_folder(get_base_from_bundle_name(bundle_name))
        runner = self.with_params(cwd=bundle_folder)
        runner.run(
            [
                "ln",
                "-sfn",
                f"{get_worktree_container_folder()}/.devcontainer",
                f"{bundle_folder}/.devcontainer",
            ]
        )
        # odoo and enterprise share one node_modules per base. We hard-link it into each worktree:
        # the worktree gets a real directory (so `npm install` won't delete it the way it deletes a
        # symlink), while the files share inodes so there is no extra disk cost. enable.sh then runs
        # idempotently - `npm install` no-ops when the tree already matches, and its enterprise copy
        # is guarded to skip when node_modules already exists.
        base_node_modules = f"{base_folder}/node_modules"
        base_lock = f"{base_folder}/package-lock.json"
        odoo_wt = get_worktree_bundle_repo_folder(bundle_name, "odoo")
        enterprise_wt = get_worktree_bundle_repo_folder(bundle_name, "enterprise")
        base_ready = self._node_modules_ready(base_node_modules)
        if base_ready:
            # Reuse: hard-link the shared node_modules into both repos and seed odoo's lockfile so
            # enable.sh's `npm install` recognises the tree as up to date instead of rebuilding it.
            for repo_wt in (odoo_wt, enterprise_wt):
                runner.run(["rm", "-rf", f"{repo_wt}/node_modules"])
                runner.run(["cp", "-al", base_node_modules, f"{repo_wt}/node_modules"])
            if os.path.exists(base_lock):
                runner.run(["cp", base_lock, f"{odoo_wt}/package-lock.json"])
        runner.run(["bash", "./odoo/addons/web/tooling/enable.sh"], input="y\n")
        if not base_ready:
            # First worktree of this base: seed the shared base with hard links from the fresh
            # build, then re-link enterprise so both repos point at the same inodes.
            runner.run(["rm", "-rf", base_node_modules])
            runner.run(["cp", "-al", f"{odoo_wt}/node_modules", base_node_modules])
            runner.run(["cp", f"{odoo_wt}/package-lock.json", base_lock])
            runner.run(["rm", "-rf", f"{enterprise_wt}/node_modules"])
            runner.run(["cp", "-al", base_node_modules, f"{enterprise_wt}/node_modules"])
        runner.run(["code", "--folder-uri", self._devcontainer_folder_uri(bundle_folder)])

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
