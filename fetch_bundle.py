"""Fetch bundle info from runbot"""

from rich import print

import argparse
import requests
import subprocess

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
    "upgrade": "odoo-dev",
}

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the bundle to fetch", type=str)
args = parser.parse_args()
bundle_name = args.name.replace("odoo-dev:", "")
wt_root_folder = f"/home/seb/src/odoo/{bundle_name}"
url = f"https://runbot.odoo.com/api/bundle?name={bundle_name}"
print(f"Fetching {url}")
response = requests.request("GET", url, timeout=3).json()
print(response)

def run(cmd, folder=None):
    print("exec: " + " ".join(cmd))
    subprocess.run(cmd, cwd=folder, check=True, text=True, capture_output=True)

run(["ln", "-sfn", "/home/seb/repo/useful-things/odools.toml", f"{wt_root_folder}/odools.toml"])
run(["ln", "-sfn", "/home/seb/src/odoo/.vscode", f"{wt_root_folder}/.vscode"])

for branch in response["branches"]:
    repo = branch["repo"]
    if branch["is_pr"]:
        continue
    repo_folder = folder_by_repo[repo]
    remote_dev = remote_dev_by_repo[repo]
    print(f"Fetching [yellow]{repo}[/yellow]")
    try:
        run(["git", "fetch", remote_dev, branch["name"]], repo_folder)
    except subprocess.CalledProcessError as e:
        print(e.stderr)
        raise
for commit in response["commits"]:
    repo = commit["repo"]
    repo_folder = folder_by_repo[repo]
    wt_repo_folder = f"{wt_root_folder}/{repo}"
    remote = remote_by_repo[repo]
    remote_dev = remote_dev_by_repo[repo]
    commit_hash = commit["name"]
    print("------------------------------------")
    print(f"Handling [yellow]{repo}[/yellow] ({repo_folder}) with {remote} at {commit_hash}")
    run(["git", "fetch", remote, commit_hash], repo_folder)
    try:
        run(["git", "worktree", "add", "-B", bundle_name, wt_repo_folder, commit_hash], repo_folder)
    except subprocess.CalledProcessError as e:
        if f"fatal: '{bundle_name}' is already used by worktree at '{wt_repo_folder}'" in e.stderr:
            pass
        elif f"fatal: '{wt_repo_folder}' already exists" in e.stderr:
            run(["git", "switch", "-C", bundle_name, f"{remote_dev}/{bundle_name}"], wt_repo_folder)
        else:
            print(e.stderr)
            raise
    try:
        run(["git", "branch", "-u", f"{remote_dev_by_repo[repo]}/{bundle_name}"], wt_repo_folder)
    except subprocess.CalledProcessError as e:
        if (
            f"fatal: could not set upstream of HEAD to {remote_dev_by_repo[repo]}/{bundle_name} when it does not point to any branch"
            in e.stderr
            or f"fatal: the requested upstream branch '{remote_dev_by_repo[repo]}/{bundle_name}' does not exist"
            in e.stderr
        ):
            pass
        else:
            print(e.stderr)
            raise(e)

run(["code", wt_root_folder])
print("[green]Done[/green]")
