"""Fetch bundle info from runbot and create/update corresponding worktrees locally.

Example:
 $ python ~/repo/useful-things/scripts/fetch_bundle.py master-bundle-name-ngram
"""

from collections import defaultdict
from rich import print
from rich.tree import Tree

import argparse
import requests

from commands import (
    clean_bundle_name,
    get_base_from_bundle_name,
    get_remote_dev_branch_name,
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
)
from utils import UtilsRunner

runner = UtilsRunner()

# change this config
ROOT = "/home/seb/repo/"

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the bundle to fetch", type=str)
args = parser.parse_args()
bundle_name = clean_bundle_name(args.name)
base = get_base_from_bundle_name(bundle_name)
wt_bundle_folder = get_worktree_bundle_folder(bundle_name)
url = f"https://runbot.odoo.com/api/bundle?name={bundle_name}"
print(f"Fetching {url}")
response = requests.request("GET", url, timeout=3).json()
print(response)

if not response.get("id"):
    print(f"Bundle [red]{bundle_name}[/red] not found on runbot or API failure")
    exit(1)

make_branch_by_repo = defaultdict(lambda: False)


def fetch_url(url):
    try:
        response = requests.get(url, timeout=5)
        return f"{url}: {response.status_code}"
    except Exception as e:
        return f"{url}: Failed due to {e}"


runner.prepare_worktree_bundle_folder(bundle_name=bundle_name)
for branch in response["branches"]:
    if not branch["is_pr"]:
        make_branch_by_repo[branch["repo"]] = True


def handle_commit(runner: UtilsRunner, commit):
    repo = commit["repo"]
    wt_repo_folder = get_worktree_bundle_repo_folder(bundle_name, repo)
    if make_branch_by_repo[repo]:
        remote_dev_branch_name = get_remote_dev_branch_name(bundle_name, repo)
        runner.git_fetch(repo=repo, dev=True, ref=bundle_name)
        runner.add_worktree(
            repo=repo,
            bundle_name=bundle_name,
            make_branch=True,
            target_ref=remote_dev_branch_name,
            track=True,
            on_existing=lambda runner: (
                runner.switch_to_branch(repo=repo, branch=bundle_name),
                runner.run(["git", "branch", "-u", remote_dev_branch_name], cwd=wt_repo_folder),
            ),
        )
    else:
        commit_hash = commit["name"]
        runner.git_fetch(repo=repo, dev=False, ref=commit_hash)
        runner.add_worktree(
            repo=repo,
            bundle_name=bundle_name,
            make_branch=False,
            target_ref=commit_hash,
            on_existing=lambda runner: runner.run(
                ["git", "checkout", commit_hash],
                cwd=wt_repo_folder,
            ),
        )


runner.parallel_run(Tree("Commits"), response["commits"], handle_commit, lambda c: c["repo"])
runner.finish_worktree_bundle_folder(bundle_name=bundle_name)
print("[green]Done[/green]")
