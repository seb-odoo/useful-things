"""Fetch all remote branches that are locally checkouted, and also fetch all sticky branches.
All other remote refs are removed.

Examples:
 $ python ~/repo/useful-things/scripts/fetch_all.py
"""

from rich import print
from rich.tree import Tree

from utils import RemoteRefManager, UtilsRunner

from commands import (
    get_remote_dev_ref,
    get_remote_dev_repo,
    get_remote_ref,
    get_remote_repo,
    get_sticky_bundles,
    get_repo_folder,
    get_repos,
)
from delete_bundle import delete_bundle

runner = UtilsRunner()

remote_ref_manager = RemoteRefManager()


def handle_repo_remote(runner: UtilsRunner, repo, remote, branch_r):
    sticky_bundles = get_sticky_bundles(repo)
    remote_branches = [
        line.removeprefix(f"{remote}/")
        for line in branch_r
        if line.startswith(f"{remote}/") and not line.startswith(f"{remote}/HEAD")
    ]
    dev = remote == get_remote_dev_repo(repo)
    if dev:
        res = runner.run(["git", "branch", "--format=%(refname:short)"], capture_output=True)
        to_fetch = [line.strip() for line in res.stdout.splitlines() if "HEAD" not in line]
    else:
        to_fetch = sticky_bundles
    if refs_to_delete := [
        get_ref(branch, repo)
        for branch in remote_branches
        if branch not in to_fetch
        for get_ref in (get_remote_ref, get_remote_dev_ref)
    ]:
        runner.run(
            ["git", "update-ref", "--stdin"],
            input="".join([f"delete {ref}\n" for ref in refs_to_delete]),
        )
    runner.git_fetch(repo=repo, dev=dev, ref=to_fetch, remote_ref_manager=remote_ref_manager)


def handle_repo(runner: UtilsRunner, repo):
    runner = runner.with_params(cwd=get_repo_folder(repo))
    res = runner.run(["git", "branch", "-r"], capture_output=True)
    branch_r = [line.strip() for line in res.stdout.splitlines()]
    for repo_remote in (get_remote_repo(repo), get_remote_dev_repo(repo)):
        handle_repo_remote(runner, repo, repo_remote, branch_r)


runner.parallel_run(Tree("Repositories"), get_repos(), handle_repo)
print("[green]Done[/green]")
for ref in remote_ref_manager.safe_to_delete_refs:
    delete_bundle(runner=runner, bundle_name=ref, force=True, also_remote=False)
for repo, ref in remote_ref_manager.repo_ref_to_clean:
    runner.delete_branch_and_remote_ref(repo=repo, bundle_name=ref)
for ref in remote_ref_manager.refs_to_prompt_for_deletion:
    if input(f"Gone ref: {ref}. Delete it? (Y/n)").lower() in ["", "y"]:
        delete_bundle(runner=runner, bundle_name=ref, force=True, also_remote=False)
