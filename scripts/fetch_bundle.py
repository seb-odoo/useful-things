"""Fetch bundle info from runbot and create/update corresponding worktrees locally.

Example: python ~/repo/useful-things/scripts/fetch_bundle.py master-bundle-name-ngram.
"""

from collections import defaultdict
from rich import print
from rich.tree import Tree

import argparse
import requests

from command_runner import live_task_executor, PrintParams, Runner
from commands import (
    clean_bundle_name,
    get_base_from_bundle_name,
    get_remote_dev_branch_name,
    get_worktree_base_folder,
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
)
from utils import add_worktree, git_fetch, prepare_worktree_bundle_folder, switch_to_branch

runner = Runner()

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

make_branch_by_repo = defaultdict(lambda: False)


def fetch_url(url):
    try:
        response = requests.get(url, timeout=5)
        return f"{url}: {response.status_code}"
    except Exception as e:
        return f"{url}: Failed due to {e}"


prepare_worktree_bundle_folder(runner=runner, bundle_name=bundle_name)
for branch in response["branches"]:
    if not branch["is_pr"]:
        make_branch_by_repo[branch["repo"]] = True


def handle_commit(commit, print_params: PrintParams):
    repo = commit["repo"]
    print_params = print_params.tree_add(repo)
    wt_repo_folder = get_worktree_bundle_repo_folder(bundle_name, repo)
    if make_branch_by_repo[repo]:
        remote_dev_branch_name = get_remote_dev_branch_name(bundle_name, repo)

        def handle_existing_worktree(print_params):
            switch_to_branch(
                runner=runner,
                cwd=wt_repo_folder,
                branch=bundle_name,
                target_ref=remote_dev_branch_name,
                print_params=print_params,
            )
            runner.run(
                ["git", "branch", "-u", remote_dev_branch_name],
                print_params=print_params,
                cwd=wt_repo_folder,
            )

        git_fetch(
            runner=runner,
            repo=repo,
            dev=True,
            ref=bundle_name,
            print_params=print_params,
        )
        add_worktree(
            runner=runner,
            repo=repo,
            bundle_name=bundle_name,
            make_branch=True,
            target_ref=remote_dev_branch_name,
            track=True,
            on_existing=handle_existing_worktree,
            print_params=print_params,
        )
    else:
        commit_hash = commit["name"]
        git_fetch(
            runner=runner,
            repo=repo,
            dev=False,
            ref=commit_hash,
            print_params=print_params,
        )
        add_worktree(
            runner=runner,
            repo=repo,
            bundle_name=bundle_name,
            make_branch=False,
            target_ref=commit_hash,
            on_existing=lambda print_params: runner.run(
                ["git", "checkout", commit_hash],
                print_params=print_params,
                cwd=wt_repo_folder,
            ),
            print_params=print_params,
        )


with live_task_executor(Tree("Commits")) as submit_task:
    for commit in response["commits"]:
        submit_task(handle_commit, commit)


for repo in ("odoo", "enterprise"):
    node_folder = f"{get_worktree_base_folder(base)}/{repo}/node_modules"
    runner.run(["mkdir", "-p", node_folder])
    runner.run(
        ["ln", "-sfn", node_folder, f"{wt_bundle_folder}/{repo}/node_modules"],
        print_params=PrintParams(),
    )
runner.run(
    ["bash", "./odoo/addons/web/tooling/enable.sh"],
    cwd=wt_bundle_folder,
    input="y\n",
    print_params=PrintParams(),
)
runner.run(["code", "."], cwd=wt_bundle_folder)
print("[green]Done[/green]")
