"""Fetch all remote branches that are locally known.

Examples:
 $ python ~/repo/useful-things/scripts/fetch_all.py
"""

from rich import print
from rich.tree import Tree

from utils import UtilsRunner, get_remote_dev_repo, get_remote_repo

from commands import get_repo_folder, get_repos

runner = UtilsRunner()


def handle_repo_remote(runner: UtilsRunner, repo_remote):
    repo, remote = repo_remote
    runner = runner.with_params(cwd=get_repo_folder(repo))
    # res = runner.run(["git", "branch", "-r"], capture_output=True)
    # remote_branches = [
    #     line.removeprefix(f"{remote}/")
    #     for line in [line.strip() for line in res.stdout.splitlines()]
    #     if line.startswith(f"{remote}/") and not line.startswith(f"{remote}/HEAD")
    # ]
    if dev := remote == get_remote_dev_repo(repo):
        res = runner.run(["git", "branch", "--format=%(refname:short)"], capture_output=True)
        local_branches = [line.strip() for line in res.stdout.splitlines() if "HEAD" not in line]
        # for branch in (branch for branch in remote_branches if branch not in local_branches):
        #     runner.delete_remote_ref(repo=repo, bundle_name=branch)
        runner.git_fetch(repo=repo, dev=dev, ref=local_branches)
    else:
        runner.git_fetch(repo=repo, dev=dev, ref=[])
        pass


def handle_repo(runner: UtilsRunner, repo):
    runner.parallel_run(
        runner.tree,
        [(repo, remote) for remote in (get_remote_repo(repo), get_remote_dev_repo(repo))],
        handle_repo_remote,
        lambda repo_remote: repo_remote[1],
    )


runner.parallel_run(Tree("Repositories"), get_repos(), handle_repo)

print("[green]Done[/green]")
