"""Fetch bundle info from runbot"""

from collections import defaultdict
from rich import print

# Record the starting time
import argparse
import requests
import subprocess
import time

start_time = time.perf_counter()

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
parts = bundle_name.split("-")
base = f"{parts[0]}-{parts[1]}" if parts[0] == "saas" else parts[0]
wt_root_folder = f"/home/seb/src/odoo"
wt_base_folder = f"{wt_root_folder}/{base}"
wt_bundle_folder = f"{wt_base_folder}/{bundle_name}"
url = f"https://runbot.odoo.com/api/bundle?name={bundle_name}"
print(f"Fetching {url}")
response = requests.request("GET", url, timeout=3).json()
print(response)

def run(cmd, *, cwd=None, handle_exceptions=None, **kwargs):
    elapsed_time = time.perf_counter() - start_time
    print(f"{elapsed_time:.2f} [yellow]From: " + (cwd or "current folder") + "[/yellow]")
    state = "✅️"
    error = None
    try:
        subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True, **kwargs)
    except subprocess.CalledProcessError as e:
        if handle_exceptions:
            for msg, handler in handle_exceptions.items():
                if msg in e.stderr:
                    handler()
                    state = "⚠️"
                    return
        state = "❌"
        error = e.stderr
        raise
    finally:
        print(state + " " + " ".join(cmd))
        if error:
            print(error)

run(["mkdir", "-p", wt_bundle_folder])
run(["ln", "-sfn", "/home/seb/repo/useful-things/odools.toml", f"{wt_bundle_folder}/odools.toml"])
run(["ln", "-sfn", f"{wt_root_folder}/.vscode", f"{wt_bundle_folder}/.vscode"])

make_branch_by_repo = defaultdict(lambda: False)
for branch in response["branches"]:
    repo = branch["repo"]
    if branch["is_pr"]:
        continue
    repo_folder = folder_by_repo[repo]
    remote_dev = remote_dev_by_repo[repo]
    print(f"Fetching [yellow]{repo}[/yellow]")
    run(["git", "fetch", remote_dev, f"+refs/heads/{branch['name']}:refs/remotes/{remote_dev}/{branch['name']}"], cwd=repo_folder)
    make_branch_by_repo[repo] = True
for commit in response["commits"]:
    repo = commit["repo"]
    repo_folder = folder_by_repo[repo]
    wt_repo_folder = f"{wt_bundle_folder}/{repo}"
    remote = remote_by_repo[repo]
    remote_dev = remote_dev_by_repo[repo]
    remote_dev_branch_name = f"{remote_dev}/{bundle_name}"
    ref =  f"refs/heads/{bundle_name}"
    commit_hash = commit["name"]
    print("------------------------------------")
    print(f"Handling [yellow]{repo}[/yellow] ({repo_folder}) with {remote} at {commit_hash}")
    run(["git", "fetch", remote, commit_hash], cwd=repo_folder)
    if make_branch_by_repo[repo]:
        run(["git", "worktree", "add", "-B", bundle_name, wt_repo_folder, remote_dev_branch_name], cwd=repo_folder, handle_exceptions={
            f"fatal: '{bundle_name}' is already used by worktree at '{wt_repo_folder}'": lambda: None,
            f"fatal: '{wt_repo_folder}' already exists": lambda: run(["git", "switch", "-C", bundle_name, remote_dev_branch_name], cwd=wt_repo_folder),
        })
        run(["git", "branch", "-u", ref], cwd=wt_repo_folder, handle_exceptions={
            f"fatal: could not set upstream of HEAD to {ref} when it does not point to any branch": lambda: None,
            f"fatal: the requested upstream branch '{ref}' does not exist": lambda: None,
        })
    else:
        run(["git", "worktree", "add", wt_repo_folder, commit_hash], cwd=repo_folder, handle_exceptions={
            f"fatal: '{wt_repo_folder}' already exists": lambda: run(["git", "checkout", commit_hash], cwd=wt_repo_folder),
        })

# files = [".eslintignore", ".eslintrc.json", "jsconfig.json", "package-lock.json", "package.json"]
for repo in ("odoo", "enterprise"):
    # for file in files:
    #     file_path = f"{wt_base_folder}/{repo}/{file}"
    #     run(["touch", file_path])
    #     run(["ln", "-sfn", file_path, f"{wt_bundle_folder}/{repo}/{file}"])
    node_folder = f"{wt_base_folder}/{repo}/node_modules"
    run(["mkdir", "-p", node_folder], handle_exceptions={})
    run(["ln", "-sfn", node_folder, f"{wt_bundle_folder}/{repo}/node_modules"])
run(
    ["bash", f"{wt_bundle_folder}/odoo/addons/web/tooling/enable.sh"],
    input="y\n",
)
run(["code", "."], cwd=wt_bundle_folder)
print("[green]Done[/green]")
