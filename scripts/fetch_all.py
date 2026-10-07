"""Fetch all remote branches that are locally checkouted, and also fetch all sticky branches.
All other remote refs are removed.

Also creates the local branch of each open PR of mine, and of each forward-port of mine waiting
on me: a conflict, a red CI, or a chain tip nobody gave r+ yet. Mergebot merges the other ones by
itself.

Also deletes a bundle branch never pushed while another repo of the bundle has an open PR of mine:
pushing the other repos alone left it out. Its worktree stays on the commit, detached. Skipped
while the bundle has an agent at work or a running dev container.

Examples:
 $ python ~/repo/useful-things/scripts/fetch_all.py
"""

import json
import os
import re
import subprocess
import time

from rich import print
from rich.tree import Tree

from utils import RemoteRefManager, UtilsRunner

from agents import read_state
from branch_status import (
    AGENT_WORKING,
    get_bundle_agent,
    get_github_repo,
    get_local_branches,
    get_open_bundle_folders,
    get_push,
    get_status,
)
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
    get_worktree_bundle_folder,
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
        return {}, {}
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
        "... on PullRequest { headRefName number repository { nameWithOwner } } } } }"
    )
    if not (out := gh("api", "graphql", "-f", f"query={query}")):
        return {}, {}
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
    open_prs_by_head = {}
    for pr in mine_heads:
        repo = repo_by_github_repo[pr["repository"]["nameWithOwner"]]
        open_prs_by_head.setdefault(pr["headRefName"], {})[repo] = pr["number"]
    return heads_by_repo, open_prs_by_head


branches_to_create_by_repo, open_prs_by_head = get_branches_to_create()


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
            [
                "git",
                "for-each-ref",
                "--format=%(refname:short) %(upstream:remotename)",
                "refs/heads/",
            ],
            capture_output=True,
        )
        to_fetch = []
        fork_branches_by_remote = {}
        for line in res.stdout.splitlines():
            branch, _, upstream_remote = line.strip().partition(" ")
            if upstream_remote in ("", get_remote_repo(repo), remote):
                to_fetch.append(branch)
            else:
                fork_branches_by_remote.setdefault(upstream_remote, []).append(branch)
        for fork_remote, branches in fork_branches_by_remote.items():
            runner.git_fetch(
                repo=repo,
                dev=dev,
                ref=branches,
                remote=fork_remote,
                remote_ref_manager=remote_ref_manager,
            )
        new_branches = [
            head for head in branches_to_create_by_repo.get(repo, []) if head not in to_fetch
        ]
        # A branch never pushed is missing from the dev remote, and git_fetch would call it gone.
        to_fetch = [branch for branch in to_fetch if branch in remote_branches] + new_branches
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


def drop_unpushed_branches(runner: UtilsRunner):
    open_folders = get_open_bundle_folders()
    agent_state = read_state()
    now = time.time()
    for repo in get_repos():
        if not os.path.isdir(get_repo_folder(repo)):
            continue
        for branch, _date, head, _in_worktree in get_local_branches(repo):
            prs = open_prs_by_head.get(branch, {})
            if not prs or repo in prs or get_push(repo, branch) is not None:
                continue
            status = get_status(repo, branch, write_tree=False)
            if not status or not status["ahead"]:
                continue
            is_open = get_worktree_bundle_folder(branch) in open_folders
            agent = get_bundle_agent(branch, agent_state, is_open, now)
            if is_open or (agent and agent["state"] in AGENT_WORKING):
                continue
            runner.delete_branch_and_remote_ref(repo=repo, bundle_name=branch)
            links = ", ".join(f"{pr_repo}#{number}" for pr_repo, number in prs.items())
            print(f"Dropped {repo}/{branch} at {head[:10]}: never pushed, PR {links}")


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
drop_unpushed_branches(runner)
