"""Fetch bundle info from runbot and create/update corresponding worktrees locally.

Examples:
 $ python ~/repo/useful-things/scripts/fetch_bundle.py master-bundle-name-ngram
 $ python ~/repo/useful-things/scripts/fetch_bundle.py https://github.com/odoo/odoo/pull/292267
 $ python ~/repo/useful-things/scripts/fetch_bundle.py contributor:20.0-fix-name
"""

import argparse
from collections import defaultdict
import re

import requests
from branch_status import PR_URL, get_github_repo, get_pr, git
from commands import (
    clean_bundle_name,
    get_base_for_repo,
    get_base_from_bundle_name,
    get_remote_branch_name,
    get_remote_dev_repo,
    get_repo_folder,
    get_repos,
    get_sticky_bundles,
    get_worktree_bundle_repo_folder,
)
from rich import print
from rich.tree import Tree
from utils import UtilsRunner

runner = UtilsRunner()

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the bundle to fetch, or a PR link", type=str)
parser.add_argument("--no-open", action="store_true", help="Do not open the bundle in VS Code")
args = parser.parse_args()
fork_remote_by_repo = {}


def get_bundle_names_from_pr(github_repo, number):
    repo = next((repo for repo in get_repos() if get_github_repo(repo) == github_repo), None)
    if not repo:
        print(f"[red]{github_repo}[/red] is none of the cloned repos")
        raise SystemExit(1)
    try:
        pr = get_pr(github_repo, number, "baseRefName,headRefName,headRepository")
    except ValueError as error:
        print(f"[red]{error}[/red]")
        raise SystemExit(1) from None
    head, base = pr["headRefName"], pr["baseRefName"]
    dev_remote = get_remote_dev_repo(repo)
    dev_github_repo = get_github_repo(repo, dev_remote)
    head_github_repo = pr["headRepository"]["nameWithOwner"]
    if head_github_repo == dev_github_repo:
        return head, head, base
    fork_remote = head_github_repo.split("/")[0]
    dev_url = git(repo, "remote", "get-url", dev_remote)
    fork_url = dev_url.replace(dev_github_repo, head_github_repo)
    runner.add_fork_remote(repo=repo, remote=fork_remote, url=fork_url)
    fork_remote_by_repo[repo] = fork_remote
    return head, f"{fork_remote}:{head}", base


def get_runbot_bundle(name):
    url = f"https://runbot.odoo.com/api/bundle?name={name}"
    print(f"Fetching {url}")
    try:
        response = requests.request("GET", url, timeout=3).json()
    except requests.RequestException as e:
        print(f"[red]No answer from runbot[/red] about {name}: {e}")
        raise SystemExit(1) from None
    print(response)
    return response


dev_remotes = {get_remote_dev_repo(repo) for repo in get_repos()}
if pr_match := PR_URL.match(args.name):
    bundle_name, runbot_bundle_name, pr_base = get_bundle_names_from_pr(*pr_match.groups())
    response = get_runbot_bundle(runbot_bundle_name)
elif (owner := args.name.rpartition(":")[0]) and owner not in dev_remotes:
    response = get_runbot_bundle(args.name)
    pr = next((branch for branch in response.get("branches", []) if branch["is_pr"]), None)
    if not pr:
        print(f"Runbot has no PR for [red]{args.name}[/red]")
        raise SystemExit(1)
    github_repo = re.search(r"github\.com[:/](.+?)(?:\.git)?$", pr["remote"])[1]
    bundle_name, _, pr_base = get_bundle_names_from_pr(github_repo, pr["name"])
else:
    bundle_name, pr_base = clean_bundle_name(args.name), None
    response = get_runbot_bundle(bundle_name)
cross_base = bool(pr_base) and not bundle_name.startswith(f"{pr_base}-")

runbot_bundle = bool(response.get("id"))
if not runbot_bundle:
    # runbot watches neither owl nor sfu: a branch pushed only there shows on the dev remotes alone.
    response = {"branches": [], "commits": []}

local_branch_by_repo = defaultdict(lambda: False)
make_branch_by_repo = defaultdict(lambda: False, dict.fromkeys(fork_remote_by_repo, True))
for branch in response["branches"]:
    if not branch["is_pr"]:
        make_branch_by_repo[branch["repo"]] = True
runbot_repos = {commit["repo"] for commit in response["commits"]} | {
    branch["repo"] for branch in response["branches"]
}


def handle_remote_branch(runner: UtilsRunner, repo):
    if (
        not make_branch_by_repo[repo]
        and repo not in runbot_repos
        and bundle_name not in get_sticky_bundles(repo)
    ):
        make_branch_by_repo[repo] = runner.remote_has_branch(repo=repo, branch=bundle_name)
    if not make_branch_by_repo[repo] and bundle_name not in get_sticky_bundles(repo):
        res = runner.run(
            ["git", "for-each-ref", "--format=%(refname)", f"refs/heads/{bundle_name}"],
            cwd=get_repo_folder(repo),
        )
        local_branch_by_repo[repo] = bool(res.stdout.strip())


runner.parallel_run(Tree("Branches"), get_repos(), handle_remote_branch)

if not runbot_bundle and not any([*make_branch_by_repo.values(), *local_branch_by_repo.values()]):
    print(f"Bundle [red]{bundle_name}[/red] found on neither runbot, the dev remotes nor locally")
    raise SystemExit(1)

runner.prepare_worktree_bundle_folder(bundle_name=bundle_name, base=pr_base)


def handle_commit(runner: UtilsRunner, commit):
    repo = commit["repo"]
    if repo not in get_repos():
        # runbot knows about all the repositories, `config.py` only about the cloned ones
        return
    wt_repo_folder = get_worktree_bundle_repo_folder(bundle_name, repo)
    if make_branch_by_repo[repo]:
        remote = fork_remote_by_repo.get(repo) or get_remote_dev_repo(repo)
        remote_branch_name = f"{remote}/{bundle_name}"
        runner.git_fetch(repo=repo, dev=True, ref=bundle_name, remote=remote)
        runner.add_worktree(
            repo=repo,
            bundle_name=bundle_name,
            make_branch=True,
            target_ref=remote_branch_name,
            track=True,
            on_existing=lambda runner: (
                runner.switch_to_branch(
                    repo=repo,
                    branch=bundle_name,
                    target_ref=remote_branch_name,
                ),
                runner.run(["git", "branch", "-u", remote_branch_name], cwd=wt_repo_folder),
            ),
        )
    elif local_branch_by_repo[repo]:
        runner.add_worktree(
            repo=repo,
            bundle_name=bundle_name,
            make_branch=False,
            target_ref=bundle_name,
            on_existing=lambda runner: runner.run(
                ["git", "switch", bundle_name],
                cwd=wt_repo_folder,
            ),
        )
    else:
        if (commit_hash := commit.get("name")) and not cross_base:
            fetch_ref = target_ref = commit_hash
        else:
            base = get_base_for_repo(get_base_from_bundle_name(bundle_name), repo)
            fetch_ref = base
            target_ref = get_remote_branch_name(base, repo)
        runner.git_fetch(repo=repo, dev=False, ref=fetch_ref)
        runner.add_worktree(
            repo=repo,
            bundle_name=bundle_name,
            make_branch=False,
            target_ref=target_ref,
            on_existing=lambda runner: runner.run(
                ["git", "checkout", target_ref],
                cwd=wt_repo_folder,
            ),
        )


# A batch still in preparation only lists the repos pushed so far, so a repo can be missing from
# "commits". Synthesize an entry for every repo, so the bundle gets all of its worktrees: a repo
# with a dev branch ignores the commit hash anyway, the others fall back to their base branch.
commit_by_repo = {commit["repo"]: commit for commit in response["commits"]}
for repo in get_repos():
    commit_by_repo.setdefault(repo, {"repo": repo})

runner.parallel_run(
    Tree("Commits"),
    list(commit_by_repo.values()),
    handle_commit,
    lambda c: c["repo"],
)
runner.finish_worktree_bundle_folder(bundle_name=bundle_name, open_window=not args.no_open)
print("[green]Done[/green]")
