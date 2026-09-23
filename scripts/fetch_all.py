"""Fetch all remote branches that are locally checkouted, and also fetch all sticky branches.
All other remote refs are removed.

Also creates the local branch of each open PR of mine, and of each forward-port of mine waiting
on me: a conflict, a red CI, or a chain tip nobody gave r+ yet. Mergebot merges the other ones by
itself.

Examples:
 $ python ~/repo/useful-things/scripts/fetch_all.py
"""

import json
import os
import re
import subprocess

from rich import print
from rich.tree import Tree

from utils import RemoteRefManager, UtilsRunner

from branch_status import get_github_repo
from commands import (
    BUNDLE_SUFFIX,
    get_remote_dev_branch_name,
    get_remote_dev_ref,
    get_remote_dev_repo,
    get_remote_ref,
    get_remote_repo,
    get_sticky_bundles,
    get_repo_folder,
    get_repos,
)
from delete_bundle import delete_bundle

FW_BOT = "fw-bot"
FW_TIP_MESSAGE = "is the last of the forward-port chain"
R_PLUS = re.compile(r"@robodoo\b.*\br\+")

runner = UtilsRunner()

remote_ref_manager = RemoteRefManager()


def gh(*args):
    res = subprocess.run(["gh", *args], capture_output=True, check=False, text=True)
    if res.returncode:
        print(f"[yellow]Branch creation skipped[/yellow]: {res.stderr.strip()}")
        return None
    return res.stdout


def needs_me(pr):
    if any(label["name"] == "conflict" for label in pr["labels"]["nodes"]):
        return True
    rollup = pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]
    if rollup and rollup["state"] in ("ERROR", "FAILURE"):
        return True
    comments = pr["comments"]["nodes"]
    tips = [
        i
        for i, comment in enumerate(comments)
        if (comment["author"] or {}).get("login") == FW_BOT and FW_TIP_MESSAGE in comment["body"]
    ]
    return bool(tips) and not any(R_PLUS.search(c["body"]) for c in comments[tips[-1] :])


def get_branches_to_create():
    if not (login := gh("api", "user", "--jq", ".login")):
        return {}
    repo_by_github_repo = {
        get_github_repo(repo): repo for repo in get_repos() if os.path.isdir(get_repo_folder(repo))
    }
    repos = " ".join(f"repo:{github_repo}" for github_repo in repo_by_github_repo)
    fw_search = f"is:pr is:open author:{FW_BOT} mentions:{login.strip()} {repos}"
    mine_search = f"is:pr is:open author:{login.strip()} {repos}"
    query = (
        f"{{ fw: search(query: {json.dumps(fw_search)}, type: ISSUE, first: 100) {{ nodes {{ "
        "... on PullRequest { headRefName repository { nameWithOwner } "
        "labels(first: 20) { nodes { name } } "
        "commits(last: 1) { nodes { commit { statusCheckRollup { state } } } } "
        "comments(last: 50) { nodes { author { login } body } } } } } "
        f"mine: search(query: {json.dumps(mine_search)}, type: ISSUE, first: 100) {{ nodes {{ "
        "... on PullRequest { headRefName repository { nameWithOwner } } } } }"
    )
    if not (out := gh("api", "graphql", "-f", f"query={query}")):
        return {}
    data = json.loads(out)["data"]
    heads_by_repo = {}
    fw_heads = [
        pr
        for pr in data["fw"]["nodes"]
        if f"{BUNDLE_SUFFIX}-" in pr["headRefName"]
        and pr["headRefName"].endswith("-fw")
        and needs_me(pr)
    ]
    mine_heads = [pr for pr in data["mine"]["nodes"] if pr["headRefName"].endswith(BUNDLE_SUFFIX)]
    for pr in fw_heads + mine_heads:
        repo = repo_by_github_repo[pr["repository"]["nameWithOwner"]]
        heads_by_repo.setdefault(repo, []).append(pr["headRefName"])
    return heads_by_repo


branches_to_create_by_repo = get_branches_to_create()


def handle_repo_remote(runner: UtilsRunner, repo, remote, branch_r):
    sticky_bundles = get_sticky_bundles(repo)
    remote_branches = [
        line.removeprefix(f"{remote}/")
        for line in branch_r
        if line.startswith(f"{remote}/") and not line.startswith(f"{remote}/HEAD")
    ]
    dev = remote == get_remote_dev_repo(repo)
    new_branches = []
    if dev:
        res = runner.run(
            ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
            capture_output=True,
        )
        to_fetch = [line.strip() for line in res.stdout.splitlines()]
        new_branches = [
            head for head in branches_to_create_by_repo.get(repo, []) if head not in to_fetch
        ]
        to_fetch += new_branches
    else:
        to_fetch = sticky_bundles
    # Prune this remote's namespace only: seb-odoo also has a `master`, origin/master must survive.
    get_ref = get_remote_dev_ref if dev else get_remote_ref
    if refs_to_delete := [
        get_ref(branch, repo) for branch in remote_branches if branch not in to_fetch
    ]:
        runner.run(
            ["git", "update-ref", "--stdin"],
            input="".join([f"delete {ref}\n" for ref in refs_to_delete]),
        )
    runner.git_fetch(repo=repo, dev=dev, ref=to_fetch, remote_ref_manager=remote_ref_manager)
    for head in new_branches:
        if repo not in remote_ref_manager.gone_repos_by_ref.get(head, ()):
            runner.run(
                ["git", "branch", "--track", head, get_remote_dev_branch_name(head, repo)],
            )


def handle_repo(runner: UtilsRunner, repo):
    runner = runner.with_params(cwd=get_repo_folder(repo))
    res = runner.run(["git", "branch", "-r"], capture_output=True)
    branch_r = [line.strip() for line in res.stdout.splitlines()]
    # sfu holds its dev branches in the upstream repo, so one remote fills both roles here.
    for repo_remote in dict.fromkeys((get_remote_repo(repo), get_remote_dev_repo(repo))):
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
