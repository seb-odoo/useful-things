"""Various utility methods."""

from collections import defaultdict
from collections.abc import Iterable
import json
import os
import re

from command_runner import Runner, ignore_error
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

# Worktrees are locked so `git worktree prune`/`git gc` can't delete them. Inside a dev
# container the base repo's .git is mounted but the worktree paths (/home/seb/src/...) are not,
# so an in-container prune/gc would otherwise see every worktree as missing and wipe the shared
# admin data. delete_bundle unlocks before removing.
WORKTREE_LOCK_REASON = "managed bundle: do not prune (paths are unmounted inside dev containers)"


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
        repo_folder = get_repo_folder(repo)
        cmd = (
            ["git", "worktree", "add"]
            + (["-B", bundle_name] if make_branch else [])
            + [target_folder, target_ref]
            + (["--track"] if track else [])
        )
        self.run(
            cmd,
            cwd=repo_folder,
            handle_exceptions=self._handle_branch_holder(
                branch=bundle_name,
                cwd=repo_folder,
                retry=cmd,
                target_folder=target_folder,
                on_existing=on_existing,
            ),
        )
        self.run(
            ["git", "worktree", "lock", "--reason", WORKTREE_LOCK_REASON, target_folder],
            cwd=repo_folder,
            handle_exceptions={f"'{target_folder}' is already locked": ignore_error},
        )

    def _handle_branch_holder(self, *, branch, cwd, retry, target_folder=None, on_existing=None):
        """Build a `handle_exceptions` handler for a command another worktree on `branch` blocks.

        The holder is either the bundle's own folder, which the caller knows how to reuse through
        `on_existing`, or a worktree created inside a dev container and never removed: its path only
        exists in the container, so on the host the registration reads `prunable` while it still
        keeps the branch checked out. Such an entry goes and the command runs again.
        """
        reuse_messages = (
            [
                f"fatal: '{target_folder}' already exists",
                f"fatal: '{branch}' is already used by worktree at '{target_folder}'",
                f"fatal: '{branch}' is already checked out at '{target_folder}'",
            ]
            if target_folder and on_existing
            else []
        )

        def handle(runner, e):
            for message in reuse_messages:
                if message in e.stderr:
                    on_existing(runner)
                    return message
            match = re.search(r"is already (?:checked out|used by worktree) at '([^']+)'", e.stderr)
            if not match or not self._release_branch_from_worktrees(
                runner.with_params(cwd=cwd), branch, prunable_only=True
            ):
                # A live worktree holds the branch: git's own error names it, so let it through.
                return None
            # Same handler on the retry: the branch was the first thing git looked at, the bundle
            # folder it finds next is the caller's to reuse.
            runner.run(retry, cwd=cwd, handle_exceptions=handle)
            return match.group(0)

        return handle

    def delete_branch_and_remote_ref(self, *, repo, bundle_name, handle_exceptions=None):
        runner = self.with_params(cwd=get_repo_folder(repo))
        runner.run(["git", "update-ref", "-d", get_remote_ref(bundle_name, repo)])
        runner.run(["git", "update-ref", "-d", get_remote_dev_ref(bundle_name, repo)])
        self._release_branch_from_worktrees(runner, bundle_name)
        runner.run(["git", "branch", "-D", bundle_name], handle_exceptions=handle_exceptions)

    def _release_branch_from_worktrees(self, runner, branch, prunable_only=False):
        """Take `branch` out of the worktrees holding it, which otherwise block `git branch -D`.

        A bundle still active in another repo keeps a worktree on the branch, and a worktree created
        inside a dev container leaves a registration behind once the container is gone, under /tmp
        for a Claude worktree and under /workspace for a test-warden server one. `prunable_only`
        keeps the live worktrees on their branch, for a caller that only needs the leftovers gone.

        Returns the paths released, so a caller can tell "nothing to release" from "recovered".
        """
        released = []
        res = runner.run(["git", "worktree", "list", "--porcelain"])
        for block in res.stdout.strip().split("\n\n"):
            fields = {}
            for line in block.splitlines():
                key, _, value = line.partition(" ")
                fields[key] = value
            if fields.get("branch") != f"refs/heads/{branch}":
                continue
            if "prunable" in fields:
                # Only this branch's registrations go: `git worktree prune` would also drop those of
                # dev container worktrees still in use, whose path never exists on the host.
                runner.run(["git", "worktree", "remove", "--force", fields["worktree"]])
            elif prunable_only:
                continue
            else:
                # Detached at the same commit, so the repos building against it see the same code.
                runner.run(["git", "-C", fields["worktree"], "switch", "--detach"])
            released.append(fields["worktree"])
        return released

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

    def _install_js_tooling(self, runner, *, bundle_name, base_folder):
        """Give the bundle's repos their node_modules and their web/tooling config files."""
        odoo_wt = get_worktree_bundle_repo_folder(bundle_name, "odoo")
        enterprise_wt = get_worktree_bundle_repo_folder(bundle_name, "enterprise")
        if not os.path.isdir(odoo_wt):
            # A repo left out of a runbot batch still in preparation has no worktree yet, and both
            # enable.sh and npm live in odoo.
            return
        has_enterprise = os.path.isdir(enterprise_wt)
        repo_wts = [odoo_wt, enterprise_wt] if has_enterprise else [odoo_wt]
        # odoo and enterprise share one node_modules per base. We hard-link it into each worktree:
        # the worktree gets a real directory (so `npm install` won't delete it the way it deletes a
        # symlink), while the files share inodes so there is no extra disk cost. enable.sh then runs
        # idempotently - `npm install` no-ops when the tree already matches.
        base_node_modules = f"{base_folder}/node_modules"
        base_lock = f"{base_folder}/package-lock.json"
        base_ready = self._node_modules_ready(base_node_modules)
        if base_ready:
            # Reuse: hard-link the shared node_modules into both repos and seed odoo's lockfile so
            # enable.sh's `npm install` recognises the tree as up to date instead of rebuilding it.
            for repo_wt in repo_wts:
                runner.run(["rm", "-rf", f"{repo_wt}/node_modules"])
                runner.run(["cp", "-al", base_node_modules, f"{repo_wt}/node_modules"])
            if os.path.exists(base_lock):
                runner.run(["cp", base_lock, f"{odoo_wt}/package-lock.json"])
        runner.run(
            ["bash", "./odoo/addons/web/tooling/enable.sh"],
            input="y\n" if has_enterprise else "n\n",
        )
        if base_ready and has_enterprise:
            # enable.sh copies community's node_modules into enterprise's, so it lands one level
            # inside the hard-linked one: drop that full copy, nothing resolves through it.
            runner.run(["rm", "-rf", f"{enterprise_wt}/node_modules/node_modules"])
        if not base_ready:
            # First worktree of this base: seed the shared base with hard links from the fresh
            # build, then re-link enterprise so both repos point at the same inodes.
            runner.run(["rm", "-rf", base_node_modules])
            runner.run(["cp", "-al", f"{odoo_wt}/node_modules", base_node_modules])
            runner.run(["cp", f"{odoo_wt}/package-lock.json", base_lock])
            if has_enterprise:
                runner.run(["rm", "-rf", f"{enterprise_wt}/node_modules"])
                runner.run(["cp", "-al", base_node_modules, f"{enterprise_wt}/node_modules"])

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
        self._install_js_tooling(runner, bundle_name=bundle_name, base_folder=base_folder)
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
                if gone_ref not in ref:
                    return
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

    def open_devcontainer(self, *, bundle_name):
        """Open VS Code attached to an existing bundle's dev container (no worktree/npm setup)."""
        bundle_folder = get_worktree_bundle_folder(bundle_name)
        self.run(["code", "--folder-uri", self._devcontainer_folder_uri(bundle_folder)])

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
        self.run(
            [
                "ln",
                "-sfn",
                f"{get_worktree_container_folder()}/.claude",
                f"{worktree_bundle_folder}/.claude",
            ],
        )

    def switch_to_branch(self, *, repo, branch, target_ref: str = None):
        cwd = get_worktree_bundle_repo_folder(branch, repo)
        if not target_ref:
            target_ref = get_remote_dev_branch_name(branch, repo)
        self.run(["git", "switch", "-C", branch, target_ref], cwd=cwd)
