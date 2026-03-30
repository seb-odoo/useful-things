"""Various utility methods."""

import fire

from config import (
    BUNDLE_SUFFIX,
    folder_by_repo,
    remote_by_repo,
    remote_dev_by_repo,
    WORKTREE_CONTAINER,
)


def get_base_from_bundle_name(bundle_name):
    """Get the base name from a bundle name."""
    parts = bundle_name.split("-")
    return f"{parts[0]}-{parts[1]}" if parts[0] == "saas" else parts[0]


def get_bundle_name_from_base_and_name(base, name):
    """Get the bundle name from the base and the name."""
    return f"{base}-{name}{BUNDLE_SUFFIX}"


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


def get_worktree_base_folder(base):
    """Get the worktree base folder name from the base."""
    return f"{WORKTREE_CONTAINER}/{base}"


def get_worktree_bundle_folder(bundle_name):
    """Get the worktree folder name from the bundle name."""
    return f"{get_worktree_base_folder(get_base_from_bundle_name(bundle_name))}/{bundle_name}"


def get_worktree_bundle_repo_folder(bundle_name, repo):
    """Get the worktree folder name from the bundle name."""
    return f"{get_worktree_bundle_folder(bundle_name)}/{repo}"


def get_worktree_container_folder():
    """Get the worktree container folder."""
    return WORKTREE_CONTAINER


def clean_bundle_name(bundle_name):
    """Get the cleaned bundle name."""
    return bundle_name.replace("odoo-dev:", "")


if __name__ == "__main__":
    fire.Fire()
