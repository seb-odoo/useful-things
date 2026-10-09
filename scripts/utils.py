"""Various utility methods."""

from collections import defaultdict
from collections.abc import Iterable
import glob
import json
import os
import re

from command_runner import Runner, ignore_error
from commands import (
    BUNDLE_SUFFIX,
    MADE,
    drop_stale_containers,
    get_base_from_bundle_name,
    get_devcontainer_config,
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
from config import HOST_BOX

WORKSPACE_FOLDER = "/workspace"

_USEFUL_THINGS_FOLDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Worktrees are locked so `git worktree prune`/`git gc` can't delete them. Inside a dev
# container the base repo's .git is mounted but the worktree paths (WORKTREE_ROOT/...) are not,
# so an in-container prune/gc would otherwise see every worktree as missing and wipe the shared
# admin data. delete_bundle unlocks before removing.
WORKTREE_LOCK_REASON = "managed bundle: do not prune (paths are unmounted inside dev containers)"

FORK_REMOTE_MARK = "bundleFork"


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
                runner.with_params(cwd=cwd),
                branch,
                prunable_only=True,
            ):
                # A live worktree holds the branch: git's own error names it, so let it through.
                return None
            # Same handler on the retry: the branch was the first thing git looked at, the bundle
            # folder it finds next is the caller's to reuse.
            runner.run(retry, cwd=cwd, handle_exceptions=handle)
            return match.group(0)

        return handle

    def add_fork_remote(self, *, repo, remote, url):
        runner = self.with_params(cwd=get_repo_folder(repo))
        if runner.run(
            ["git", "remote", "add", remote, url],
            handle_exceptions={f"error: remote {remote} already exists.": ignore_error},
        ):
            runner.run(["git", "config", f"remote.{remote}.{FORK_REMOTE_MARK}", "true"])

    def delete_branch_and_remote_ref(self, *, repo, bundle_name, handle_exceptions=None):
        runner = self.with_params(cwd=get_repo_folder(repo))
        runner.run(["git", "update-ref", "-d", get_remote_ref(bundle_name, repo)])
        runner.run(["git", "update-ref", "-d", get_remote_dev_ref(bundle_name, repo)])
        self._release_branch_from_worktrees(runner, bundle_name)
        upstream_format = "--format=%(upstream:remotename)"
        res = runner.run(["git", "for-each-ref", upstream_format, f"refs/heads/{bundle_name}"])
        upstream_remote = res.stdout.strip()
        runner.run(["git", "branch", "-D", bundle_name], handle_exceptions=handle_exceptions)
        if upstream_remote:
            self._remove_unused_fork_remote(runner, upstream_remote)

    def _remove_unused_fork_remote(self, runner, remote):
        mark = f"remote.{remote}.{FORK_REMOTE_MARK}"
        res = runner.run(["git", "config", "--bool", "--default", "false", mark])
        if res.stdout.strip() != "true":
            return
        res = runner.run(["git", "for-each-ref", "--format=%(upstream:remotename)", "refs/heads/"])
        if remote not in res.stdout.split():
            runner.run(["git", "remote", "remove", remote])

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
        config_file = get_devcontainer_config(bundle_folder)
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
        try:
            with open(config_file) as file:
                declared = re.search(r'"workspaceFolder"\s*:\s*"(/[^"$]*)"', file.read())
        except OSError:
            declared = None
        workspace_folder = declared[1] if declared else WORKSPACE_FOLDER
        return f"vscode-remote://dev-container+{hex_authority}/{workspace_folder}"

    @staticmethod
    def _node_modules_ready(node_modules):
        """A node_modules is usable once npm install has created its `.bin` directory."""
        return os.path.isdir(os.path.join(node_modules, ".bin"))

    def _seed_base_node_modules(self, runner, base_folder):
        """Take the node_modules of a bundle of this base that has one, as the shared one."""
        ready = sorted(glob.glob(f"{glob.escape(base_folder)}/*/odoo/node_modules/.bin"))
        if ready:
            odoo_wt = os.path.dirname(os.path.dirname(ready[0]))
            runner.run(["rm", "-rf", f"{base_folder}/node_modules"])
            runner.run(["cp", "-al", f"{odoo_wt}/node_modules", f"{base_folder}/node_modules"])
            runner.run(["cp", f"{odoo_wt}/package-lock.json", f"{base_folder}/package-lock.json"])

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
        if not self._node_modules_ready(base_node_modules):
            self._seed_base_node_modules(runner, base_folder)
        base_ready = self._node_modules_ready(base_node_modules)
        if base_ready:
            # Reuse: hard-link the shared node_modules into both repos and seed odoo's lockfile so
            # enable.sh's `npm install` recognises the tree as up to date instead of rebuilding it.
            for repo_wt in repo_wts:
                runner.run(["rm", "-rf", f"{repo_wt}/node_modules"])
                runner.run(["cp", "-al", base_node_modules, f"{repo_wt}/node_modules"])
            if os.path.exists(base_lock):
                runner.run(["cp", base_lock, f"{odoo_wt}/package-lock.json"])
        enable = ["bash", "./odoo/addons/web/tooling/enable.sh"]
        if HOST_BOX:
            # enable.sh and what npm runs come from the fetched branch: boxed, with no network.
            offline = [f"npm_config_{key}" for key in ("offline=true", "audit=false", "fund=false")]
            folder = get_worktree_bundle_folder(bundle_name)
            enable = [HOST_BOX, "--dir", folder, "--", "env", *offline, *enable]
        runner.run(enable, input="y\n" if has_enterprise else "n\n")
        if base_ready and has_enterprise:
            # enable.sh copies community's node_modules into enterprise's, so it lands one level
            # inside the hard-linked one: drop that full copy, nothing resolves through it.
            runner.run(["rm", "-rf", f"{enterprise_wt}/node_modules/node_modules"])
        if not base_ready and not self._node_modules_ready(f"{odoo_wt}/node_modules"):
            print(
                "No node_modules yet for this base: run odoo/addons/web/tooling/enable.sh in the"
                " bundle's container, the next bundle of the base takes it from there.",
            )
        elif not base_ready:
            # First worktree of this base: seed the shared base with hard links from the fresh
            # build, then re-link enterprise so both repos point at the same inodes.
            runner.run(["rm", "-rf", base_node_modules])
            runner.run(["cp", "-al", f"{odoo_wt}/node_modules", base_node_modules])
            runner.run(["cp", f"{odoo_wt}/package-lock.json", base_lock])
            if has_enterprise:
                runner.run(["rm", "-rf", f"{enterprise_wt}/node_modules"])
                runner.run(["cp", "-al", base_node_modules, f"{enterprise_wt}/node_modules"])

    def finish_worktree_bundle_folder(self, *, bundle_name, open_window=True):
        bundle_folder = get_worktree_bundle_folder(bundle_name)
        base_folder = get_worktree_base_folder(get_base_from_bundle_name(bundle_name))
        runner = self.with_params(cwd=bundle_folder)
        runner.run(["touch", f"{bundle_folder}/{MADE}"])
        self._install_js_tooling(runner, bundle_name=bundle_name, base_folder=base_folder)
        if open_window:
            runner.open_devcontainer_folder(folder=bundle_folder)

    def git_fetch(
        self,
        *,
        repo,
        dev,
        ref=None,
        remote=None,
        remote_ref_manager: RemoteRefManager = None,
    ):
        if ref is not None and not ref:
            return
        if ref is None:
            ref = []
        fetch_remote = remote or (get_remote_dev_repo(repo) if dev else get_remote_repo(repo))
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
                    remote=remote,
                    remote_ref_manager=remote_ref_manager,
                )
                return gone_ref

        self.run(
            ["git", "fetch", fetch_remote, *ref, "-p"],
            cwd=get_repo_folder(repo),
            handle_exceptions=handle_fetch_exception,
            on_success=lambda: [
                remote_ref_manager.add_valid(repo, r) if remote_ref_manager else None for r in ref
            ],
        )

    def open_devcontainer(self, *, bundle_name):
        """Open VS Code attached to an existing bundle's dev container (no worktree/npm setup)."""
        self.open_devcontainer_folder(folder=get_worktree_bundle_folder(bundle_name))

    def open_devcontainer_folder(self, *, folder):
        """Same, for any folder holding a .devcontainer, bundle or not."""
        drop_stale_containers(folder)
        self.run(["code", "--folder-uri", self._devcontainer_folder_uri(folder)])

    def prepare_worktree_bundle_folder(self, *, bundle_name, base=None):
        worktree_bundle_folder = (
            f"{get_worktree_base_folder(base)}/{bundle_name}"
            if base
            else get_worktree_bundle_folder(bundle_name)
        )
        self.run(["mkdir", "-p", worktree_bundle_folder])
        self.run(
            [
                "ln",
                "-sfn",
                f"{_USEFUL_THINGS_FOLDER}/odools.toml",
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

    def remote_has_branch(self, *, repo, branch):
        """Say whether the dev remote of `repo` carries `branch`."""
        res = self.run(
            ["git", "ls-remote", "--heads", get_remote_dev_repo(repo), f"refs/heads/{branch}"],
            cwd=get_repo_folder(repo),
        )
        return bool(res.stdout.strip())

    def switch_to_branch(self, *, repo, branch, target_ref: str = None):
        cwd = get_worktree_bundle_repo_folder(branch, repo)
        if not target_ref:
            target_ref = get_remote_dev_branch_name(branch, repo)
        self.run(["git", "switch", "-C", branch, target_ref], cwd=cwd)
