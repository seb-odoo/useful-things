"""Fetch bundle info from runbot"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from rich import print
from rich.live import Live
from rich.tree import Tree

import argparse
import requests

from command_runner import ignore_error, PrintParams, Runner
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


def handle_branch(branch, live, tree):
    repo = branch["repo"]
    if branch["is_pr"]:
        return
    tree_repo = tree.add(repo)
    live.refresh()
    repo_folder = folder_by_repo[repo]
    remote_dev = remote_dev_by_repo[repo]
    runner.run(
        [
            "git",
            "fetch",
            remote_dev,
            f"+refs/heads/{branch['name']}:refs/remotes/{remote_dev}/{branch['name']}",
        ],
        print_params=PrintParams(live, tree_repo),
        cwd=repo_folder,
    )
    make_branch_by_repo[repo] = True


tree = Tree("Branches")
with Live(tree, auto_refresh=False) as live:
    futures = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        for branch in response["branches"]:
            futures.append(executor.submit(handle_branch, branch, live, tree))
    for future in futures:
        future.result()
    live.refresh()


def handle_commit(commit, live, tree):
    repo = commit["repo"]
    tree_repo = tree.add(repo)
    live.refresh()
    repo_folder = folder_by_repo[repo]
    wt_repo_folder = f"{wt_bundle_folder}/{repo}"
    remote = remote_by_repo[repo]
    remote_dev = remote_dev_by_repo[repo]
    remote_dev_branch_name = f"{remote_dev}/{bundle_name}"
    ref = f"refs/heads/{bundle_name}"
    commit_hash = commit["name"]
    runner.run(
        ["git", "fetch", remote, commit_hash],
        print_params=PrintParams(live, tree_repo),
        cwd=repo_folder,
    )
    if make_branch_by_repo[repo]:
        runner.run(
            ["git", "worktree", "add", "-B", bundle_name, wt_repo_folder, remote_dev_branch_name],
            print_params=PrintParams(live, tree_repo),
            cwd=repo_folder,
            handle_exceptions={
                f"fatal: '{bundle_name}' is already used by worktree at '{wt_repo_folder}'": ignore_error,
                f"fatal: '{wt_repo_folder}' already exists": lambda live, tree: runner.run(
                    ["git", "switch", "-C", bundle_name, remote_dev_branch_name],
                    print_params=PrintParams(live, tree),
                    cwd=wt_repo_folder,
                ),
            },
        )
        runner.run(
            ["git", "branch", "-u", ref],
            print_params=PrintParams(live, tree_repo),
            cwd=wt_repo_folder,
            handle_exceptions={
                f"fatal: could not set upstream of HEAD to {ref} when it does not point to any branch": ignore_error,
                f"fatal: the requested upstream branch '{ref}' does not exist": ignore_error,
            },
        )
    else:
        runner.run(
            ["git", "worktree", "add", wt_repo_folder, commit_hash],
            print_params=PrintParams(live, tree_repo),
            cwd=repo_folder,
            handle_exceptions={
                f"fatal: '{wt_repo_folder}' already exists": lambda live, tree: runner.run(
                    ["git", "checkout", commit_hash],
                    print_params=PrintParams(live, tree),
                    cwd=wt_repo_folder,
                ),
            },
        )


tree = Tree("Commits")
with Live(tree, auto_refresh=False) as live:
    futures = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        for commit in response["commits"]:
            futures.append(executor.submit(handle_commit, commit, live, tree))
    for future in futures:
        future.result()
    live.refresh()

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
    ["bash", f"{wt_bundle_folder}/odoo/addons/web/tooling/enable.sh"],
    input="y\n",
    print_params=PrintParams(),
)
runner.run(["code", "."], cwd=wt_bundle_folder)
print("[green]Done[/green]")
