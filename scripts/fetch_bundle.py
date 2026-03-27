"""Fetch bundle info from runbot"""

from collections import defaultdict
from functools import partial
from rich import print
from rich.tree import Tree

import argparse
import requests

from command_runner import ignore_error, live_task_executor, PrintParams, Runner
from utils import get_base_from_bundle_name

runner = Runner()

# change this config
ROOT = "/home/seb/repo/"
folder_by_repo = {
    "design-themes": f"{ROOT}design-themes",
    "documentation": f"{ROOT}documentation",
    "enterprise": f"{ROOT}enterprise",
    "odoo": f"{ROOT}odoo",
    "upgrade-util": f"{ROOT}upgrade-util",
    "upgrade": f"{ROOT}upgrade",
}
remote_by_repo = {
    "design-themes": "odoo",
    "documentation": "odoo",
    "enterprise": "odoo",
    "odoo": "odoo",
    "upgrade-util": "origin",
    "upgrade": "odoo",
}
remote_dev_by_repo = {
    "design-themes": "odoo-dev",
    "documentation": "odoo-dev",
    "enterprise": "odoo-dev",
    "odoo": "odoo-dev",
    "upgrade-util": "origin",
    "upgrade": "odoo",
}

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the bundle to fetch", type=str)
args = parser.parse_args()
bundle_name = args.name.replace("odoo-dev:", "")
base = get_base_from_bundle_name(bundle_name)
wt_root_folder = "/home/seb/src/odoo"
wt_base_folder = f"{wt_root_folder}/{base}"
wt_bundle_folder = f"{wt_base_folder}/{bundle_name}"
url = f"https://runbot.odoo.com/api/bundle?name={bundle_name}"
print(f"Fetching {url}")
response = requests.request("GET", url, timeout=3).json()
print(response)

runner.run(["mkdir", "-p", wt_bundle_folder])
runner.run(
    ["ln", "-sfn", "/home/seb/repo/useful-things/odools.toml", f"{wt_bundle_folder}/odools.toml"],
)
runner.run(
    ["ln", "-sfn", f"{wt_root_folder}/.vscode", f"{wt_bundle_folder}/.vscode"],
)

make_branch_by_repo = defaultdict(lambda: False)


def fetch_url(url):
    try:
        response = requests.get(url, timeout=5)
        return f"{url}: {response.status_code}"
    except Exception as e:
        return f"{url}: Failed due to {e}"


def handle_branch(branch, print_params: PrintParams):
    repo = branch["repo"]
    if branch["is_pr"]:
        return
    print_params = print_params.tree_add(repo)
    repo_folder = folder_by_repo[repo]
    remote_dev = remote_dev_by_repo[repo]
    runner.run(
        [
            "git",
            "fetch",
            remote_dev,
            f"+refs/heads/{branch['name']}:refs/remotes/{remote_dev}/{branch['name']}",
        ],
        print_params=print_params,
        cwd=repo_folder,
    )
    make_branch_by_repo[repo] = True


with live_task_executor(Tree("Branches")) as submit_task:
    for branch in response["branches"]:
        submit_task(handle_branch, branch)


def switch_to_branch(*, cwd, branch, target, print_params: PrintParams):
    runner.run(["git", "switch", "-C", branch, target], print_params=print_params, cwd=cwd)


def handle_commit(commit, print_params: PrintParams):
    repo = commit["repo"]
    print_params = print_params.tree_add(repo)
    repo_folder = folder_by_repo[repo]
    wt_repo_folder = f"{wt_bundle_folder}/{repo}"
    remote = remote_by_repo[repo]
    remote_dev = remote_dev_by_repo[repo]
    remote_dev_branch_name = f"{remote_dev}/{bundle_name}"
    ref = f"refs/heads/{bundle_name}"
    commit_hash = commit["name"]
    runner.run(["git", "fetch", remote, commit_hash], print_params=print_params, cwd=repo_folder)
    if make_branch_by_repo[repo]:
        switch_branch_callback = partial(
            switch_to_branch,
            cwd=wt_repo_folder,
            branch=bundle_name,
            target=remote_dev_branch_name,
        )
        runner.run(
            ["git", "worktree", "add", "-B", bundle_name, wt_repo_folder, remote_dev_branch_name],
            print_params=print_params,
            cwd=repo_folder,
            handle_exceptions={
                f"fatal: '{bundle_name}' is already used by worktree at '{wt_repo_folder}'": switch_branch_callback,
                f"fatal: '{wt_repo_folder}' already exists": switch_branch_callback,
            },
        )
        runner.run(
            ["git", "branch", "-u", ref],
            print_params=print_params,
            cwd=wt_repo_folder,
            handle_exceptions={
                f"fatal: could not set upstream of HEAD to {ref} when it does not point to any branch": ignore_error,
                f"fatal: the requested upstream branch '{ref}' does not exist": ignore_error,
            },
        )
    else:
        runner.run(
            ["git", "worktree", "add", wt_repo_folder, commit_hash],
            print_params=print_params,
            cwd=repo_folder,
            handle_exceptions={
                f"fatal: '{wt_repo_folder}' already exists": lambda print_params: runner.run(
                    ["git", "checkout", commit_hash],
                    print_params=print_params,
                    cwd=wt_repo_folder,
                ),
            },
        )


with live_task_executor(Tree("Commits")) as submit_task:
    for commit in response["commits"]:
        submit_task(handle_commit, commit)


# files = [".eslintignore", ".eslintrc.json", "jsconfig.json", "package-lock.json", "package.json"]
for repo in ("odoo", "enterprise"):
    # for file in files:
    #     file_path = f"{wt_base_folder}/{repo}/{file}"
    #     run(["touch", file_path])
    #     run(["ln", "-sfn", file_path, f"{wt_bundle_folder}/{repo}/{file}"])
    node_folder = f"{wt_base_folder}/{repo}/node_modules"
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
