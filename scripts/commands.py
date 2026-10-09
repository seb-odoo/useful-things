"""Various utility methods."""

import glob
import os
import subprocess
import sys

import fire

from config import (
    BUNDLE_SUFFIX,
    FILESTORE_CONTAINER,
    MASTER_ONLY_REPOS,
    STICKY_BUNDLES,
    folder_by_repo,
    remote_by_repo,
    remote_dev_by_repo,
    WORKTREE_CONTAINER,
)

MADE = ".made"


def get_base_for_repo(base, repo):
    """Get the base a given repo builds against: repos without version branches live on master."""
    return "master" if repo in MASTER_ONLY_REPOS else base


def get_base_from_bundle_name(bundle_name):
    """Get the base name from a bundle name, or from the folder of a PR fetched on another base."""
    parts = bundle_name.split("-")
    base = f"{parts[0]}-{parts[1]}" if parts[0] == "saas" else parts[0]
    if os.path.isdir(f"{WORKTREE_CONTAINER}/{base}/{bundle_name}"):
        return base
    folders = glob.glob(f"{glob.escape(WORKTREE_CONTAINER)}/*/{glob.escape(bundle_name)}")
    return os.path.basename(os.path.dirname(folders[0])) if len(folders) == 1 else base


def get_bundle_name_from_base_and_name(base, name):
    """Get the bundle name from the base and the name."""
    return f"{base}-{name}{BUNDLE_SUFFIX}"


def get_filestore_bundle_prefix(bundle_name):
    """Get the path the filestores of a bundle start with, one per database.

    Without the bundle suffix, which no database name carries: a test run on `master-x--seb`
    builds `master-x-claude-hoot`, and a prefix keeping the suffix matches none of them.
    """
    return f"{FILESTORE_CONTAINER}/{bundle_name.removesuffix(BUNDLE_SUFFIX)}"


def get_remote_branch_name(bundle_name, repo):
    """Get the remote branch name for a given bundle name and repo."""
    return f"{get_remote_repo(repo)}/{bundle_name}"


def get_remote_dev_repo(repo):
    """Get the remote dev repo for a given repo."""
    return remote_dev_by_repo[repo]


def get_remote_dev_branch_name(bundle_name, repo):
    """Get the remote dev branch name for a given bundle name and repo."""
    return f"{get_remote_dev_repo(repo)}/{bundle_name}"


def get_remote_ref(bundle_name, repo):
    """Get the remote ref for a given bundle name and repo."""
    return f"refs/remotes/{get_remote_branch_name(bundle_name, repo)}"


def get_remote_dev_ref(bundle_name, repo):
    """Get the remote ref for a given bundle name and repo."""
    return f"refs/remotes/{get_remote_dev_branch_name(bundle_name, repo)}"


def get_remote_repo(repo):
    """Get the remote repo for a given repo."""
    return remote_by_repo[repo]


def get_repo_folder(repo):
    """Get the repo folder for a given repo."""
    return folder_by_repo[repo]


def get_repos():
    """Get the list of repos."""
    return folder_by_repo.keys()


def get_sticky_bundles(repo):
    """Get the list of sticky bundles."""
    if repo in MASTER_ONLY_REPOS:
        return ["master"]
    return STICKY_BUNDLES


def get_worktree_base_folder(base):
    """Get the worktree base folder name from the base."""
    return f"{WORKTREE_CONTAINER}/{base}"


def get_worktree_base_repo(base, repo):
    """Get the worktree base folder name from the base for a given repo."""
    return f"{WORKTREE_CONTAINER}/{base}/{repo}"


def get_worktree_bundle_folder(bundle_name):
    """Get the worktree folder name from the bundle name."""
    return f"{get_worktree_base_folder(get_base_from_bundle_name(bundle_name))}/{bundle_name}"


def get_worktree_bundle_repo_folder(bundle_name, repo):
    """Get the worktree folder name from the bundle name."""
    return f"{get_worktree_bundle_folder(bundle_name)}/{repo}"


def get_worktree_container_folder():
    """Get the worktree container folder."""
    return WORKTREE_CONTAINER


def get_devcontainer_config(folder):
    """The dev container config of a folder: its own, or for a bundle the one all bundles share.

    The shared one is named by its own path and never through the bundle: a container writes its
    bundle folder, and the config says what the host runs and mounts at the next start.
    """
    if os.path.realpath(folder).startswith(f"{os.path.realpath(WORKTREE_CONTAINER)}/"):
        folder = WORKTREE_CONTAINER
    return f"{folder}/.devcontainer/devcontainer.json"


def get_stale_containers(rows, config, changed):
    """Split the containers a folder has on an older config: the stopped ones, the running ones.

    `rows` holds one (id, state, creation time, config label) per container, `changed` is when the
    config was last written.
    """
    stale = [
        (container, state)
        for container, state, created, label in rows
        if label != config or created < changed
    ]
    return (
        [container for container, state in stale if state != "running"],
        [container for container, state in stale if state == "running"],
    )


def drop_stale_containers(folder, out=sys.stdout):
    """Remove the stopped containers of a bundle folder that were made on an older config.

    A container keeps the mounts and the environment it was made with, and a start takes the one
    that carries the labels of the folder again, whatever the config says since. A bundle container
    holds nothing of its own (the workspace, the databases and the filestore are mounts), so the
    next start makes a new one. A running one is left alone and named in `out`.
    """
    config = get_devcontainer_config(folder)
    if config != get_devcontainer_config(WORKTREE_CONTAINER) or not os.path.isfile(config):
        return
    row = '{{.ID}} {{.State}} {{.Created.Unix}} {{index .Labels "devcontainer.config_file"}}'
    listed = subprocess.run(
        ["podman", "ps", "--all", "--filter", f"label=devcontainer.local_folder={folder}"]
        + ["--format", row],
        capture_output=True,
        check=False,
        text=True,
    ).stdout
    rows = [
        (container, state, int(created), label)
        for container, state, created, label in (line.split(" ", 3) for line in listed.splitlines())
    ]
    stopped, running = get_stale_containers(rows, config, os.path.getmtime(config))
    if stopped:
        subprocess.run(["podman", "rm", *stopped], capture_output=True, check=False)
    if running:
        print(
            f"{os.path.basename(folder)} runs in a container made on an older config:"
            " close its window and open it again",
            file=out,
            flush=True,
        )


def clean_bundle_name(bundle_name):
    """Get the cleaned bundle name, taking the dev remote prefix runbot displays.

    A git ref name cannot hold a `:`, so whatever comes before it is that prefix, `odoo-dev:` for
    most repos but the owl fork's remote for owl.
    """
    return bundle_name.split(":")[-1]


if __name__ == "__main__":
    fire.Fire()
