"""Fetch bundle info from runbot"""

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
url = f"https://runbot.odoo.com/api/bundle?name={bundle_name}"
print(f"Fetching {url}")
response = requests.request("GET", url, timeout=3).json()
print(response)

done = set()
processes = []

def run(cmd, folder):
    print("exec: " + " ".join(cmd))
    subprocess.run(cmd, cwd=folder, check=True)


for branch in response["branches"]:
    if branch["is_pr"]:
        continue
    # remote format examples: git@github.com:odoo/odoo, git@github.com:odoo/upgrade-util.git
    remote, repo = branch["remote"].replace("git@github.com:", "").replace(".git", "").split("/")
    done.add(repo)
    folder = folder_by_repo[repo]
    remote = remote_dev_by_repo[repo] if "odoo-dev" in remote else remote_by_repo[repo]
    branch = branch["name"]
    print("------------------------------------")
    print(f"Handling {repo} ({folder}) with {remote} at {branch}")
    run(["git", "fetch", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"], folder)
    run(["git", "switch", "-C", branch, f"{remote}/{branch}"], folder)

for commit in response["commits"]:
    repo = commit["repo"]
    if repo in done:
        continue
    folder = folder_by_repo[repo]
    remote = remote_by_repo[repo]
    commit_hash = commit["name"]
    print("------------------------------------")
    print(f"Handling {repo} ({folder}) with {remote} at {commit_hash}")
    run(["git", "fetch", remote, commit_hash], folder)
    run(["git", "switch", "-d", commit_hash], folder)

for p in processes:
    p.wait()

print("Done")
