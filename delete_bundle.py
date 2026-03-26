"""Fully delete a bundle locally."""

from rich import print

import argparse
import glob
import subprocess
import time

start_time = time.perf_counter()

ROOT = "/home/seb/repo"
repos = ["design-themes", "documentation", "enterprise", "odoo", "upgrade-util", "upgrade"]
remote_dev_by_repo = {
    "design-themes": "odoo-dev",
    "documentation": "odoo-dev",
    "enterprise": "odoo-dev",
    "odoo": "odoo-dev",
    "upgrade-util": "origin",
    "upgrade": "odoo",
}

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the branch to delete", type=str)
args = parser.parse_args()
bundle_name = args.name.replace("odoo-dev:", "")
parts = bundle_name.split("-")
base = f"{parts[0]}-{parts[1]}" if parts[0] == "saas" else parts[0]
wt_root_folder = f"/home/seb/src/odoo/{base}/{bundle_name}"

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

for repo in repos:
    repo_folder = f"{ROOT}/{repo}"
    repo_wt_root_folder = f"{wt_root_folder}/{repo}"
    run(
        ["git", "worktree", "remove", repo_wt_root_folder],
        cwd=repo_folder,
        handle_exceptions={
            f"fatal: '{repo_wt_root_folder}' is not a working tree": lambda: None,
        },
    )
    run(
        ["git", "update-ref", "-d", f"refs/remotes/{remote_dev_by_repo[repo]}/{bundle_name}"],
        cwd=repo_folder,
    )
    run(
        ["git", "branch", "-D", bundle_name],
        cwd=repo_folder,
        handle_exceptions={f"error: branch '{bundle_name}' not found": lambda: None},
    )
run(["rm", "-rf", wt_root_folder])

path_pattern = f"/home/seb/.local/share/Odoo/filestore/{bundle_name}*"
matching_files = glob.glob(path_pattern)

for matching_file in matching_files:
    if not matching_file.startswith(f"/home/seb/.local/share/Odoo/filestore/{bundle_name}"):
        print(f"[red]Skipping {matching_file} as it doesn't match the expected pattern[/red]")
        continue
    run(["rm", "-rf", matching_file])
    last_part = matching_file.split("/")[-1]
    run(
        ["dropdb", last_part],
        handle_exceptions={
            f'ERROR:  database "{last_part}" does not exist': lambda: None,
            f'dropdb: error: database removal failed: ERROR:  database "{last_part}" does not exist': lambda: None,
        },
    )

print("[green]Done[/green]")
