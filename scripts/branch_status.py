"""List local bundle branches like `git branch`, with what is not pushed, how far behind their base
they are, whether a rebase would conflict, and their PR with its review and CI state.

Reads local refs only: run `gfa` first for fresh numbers.

Examples:
 $ python ~/repo/useful-things/scripts/branch_status.py
 $ python ~/repo/useful-things/scripts/branch_status.py --json
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import functools
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

from rich.console import Console
from rich.style import Style
from rich.table import Table
from rich.text import Text

from agents import LAUNCH_GRACE, get_agent, read_state
from commands import (
    BUNDLE_SUFFIX,
    get_base_for_repo,
    get_base_from_bundle_name,
    get_remote_dev_ref,
    get_remote_dev_repo,
    get_remote_ref,
    get_remote_repo,
    get_repo_folder,
    get_repos,
    get_sticky_bundles,
    get_worktree_bundle_folder,
)

AGE_UNITS = (("d", 86400), ("h", 3600), ("m", 60))
AGENT_TAGS = {
    "building": "[dim]building[/dim]",
    "queued": "[dim]queued {queued}[/dim]",
    "running": "[dim]running[/dim]",
    "stopped": "[yellow]agent stopped[/yellow]",
    "waiting": "[dim]waiting[/dim]",
}
AGENT_WORKING = ("building", "queued", "running", "waiting")
ASK_SEVERITIES = {"dim": 2, "red": 0, "yellow": 1}
ASKED_STYLES = ((7 * 86400, "red"), (2 * 86400, "yellow"), (0, "dim"))
AUTO_ASKED_STYLES = ((7 * 86400, "red"), (86400, "yellow"), (0, "dim"))
BEHIND_STYLES = ((501, "red"), (150, "yellow"), (0, "default"))
CI_RUNNING = "[dim]ci running[/dim]"
DELEGATE = re.compile(r"@robodoo\b.*\bdelegate[+=]")
GROUPS = {
    "open": "Running",
    "me": "Waits on me",
    "drafts": "Drafts",
    "reviewer": "Waits on a reviewer",
    "ci": "Waits on CI",
    "agents": "Waits on agents",
    "mergebot": "Waits on mergebot",
    "others": "Not mine",
}
GROUP_PRECEDENCE = ("me", "ci", "drafts", "reviewer", "mergebot", "others")
ISSUE_RANKS = {"review": 0, "wip": 2, "red": 1}
MAIN_CHECK_BY_REPO = {
    "design-themes": "ci/design-theme",
    "documentation": "ci/documentation",
    "enterprise": "ci/runbot",
    "odoo": "ci/runbot",
    "sfu": "ci/sfu",
    "upgrade": "ci/runbot",
    "upgrade-util": "ci/runbot",
}
MERGEBOT_TAGS = {
    "approved": "[green]r+[/green]",
    "error": "[red]staging error[/red]",
    "ready": "[green]r+ ready[/green]",
    "staged": "[green]staged[/green]",
}
MERGEBOT_TIMEOUT = 3
MINOR_CHECKS = {"ci/security": "dim", "ci/style": "yellow"}
PR_STYLES = {"closed": "red", "draft": "dim", "merged": "magenta"}
PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
PRS_PER_QUERY = 5
R_PLUS_STATES = ("approved", "merged", "ready", "staged")
REVIEW_TAGS = {"APPROVED": "approved", "CHANGES_REQUESTED": "[red]changes[/red]"}


def git(repo, *args):
    res = subprocess.run(
        ["git", *args],
        capture_output=True,
        check=False,
        cwd=get_repo_folder(repo),
        text=True,
    )
    return res.stdout.strip() if res.returncode == 0 else None


def get_open_bundle_folders():
    try:
        out = subprocess.run(
            [
                "podman",
                "ps",
                "--filter",
                "label=devcontainer.local_folder",
                "--format",
                '{{index .Labels "devcontainer.local_folder"}}',
            ],
            capture_output=True,
            check=False,
            text=True,
        ).stdout
    except OSError:
        return set()
    return set(out.split())


def has_write_tree():
    version = re.search(r"(\d+)\.(\d+)", subprocess.check_output(["git", "--version"], text=True))
    return (int(version[1]), int(version[2])) >= (2, 38)


def get_local_branches(repo):
    out = git(
        repo,
        "for-each-ref",
        "--format=%(refname:short) %(committerdate:unix) %(objectname) %(worktreepath)",
        "refs/heads/",
    )
    return [
        (name, int(date), head, any(path))
        for name, date, head, *path in (line.split(" ", 3) for line in out.splitlines())
    ]


@functools.cache
def get_github_repo(repo, remote=None):
    url = git(repo, "remote", "get-url", remote or get_remote_repo(repo))
    return re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)[1]


def get_pr(github_repo, number, fields):
    res = subprocess.run(
        ["gh", "pr", "view", number, "-R", github_repo, "--json", fields],
        capture_output=True,
        check=False,
        text=True,
    )
    if res.returncode:
        raise ValueError(f"gh failed on {github_repo}#{number}: {res.stderr.strip()}")
    return json.loads(res.stdout)


def read_prs(pairs):
    queries = []
    for i, (repo, branch) in enumerate(pairs):
        owner, name = get_github_repo(repo).split("/")
        queries.append(
            f'p{i}: repository(owner: "{owner}", name: "{name}") {{ pullRequests('
            f"headRefName: {json.dumps(branch)}, first: 1, "
            "orderBy: {field: CREATED_AT, direction: DESC}) "
            "{ nodes { createdAt isDraft number reviewDecision state url "
            "comments(last: 30) { nodes { author { login } body createdAt } } "
            "reviews(last: 30) { nodes { body } } "
            "timelineItems(last: 20, itemTypes: [REVIEW_REQUESTED_EVENT]) { nodes { "
            "... on ReviewRequestedEvent { actor { login } createdAt } } } "
            "reviewThreads(first: 100) { nodes { isResolved "
            "comments(last: 1) { nodes { author { login } createdAt } } } } "
            "forcePushes: timelineItems(last: 1, itemTypes: [HEAD_REF_FORCE_PUSHED_EVENT]) { "
            "nodes { ... on HeadRefForcePushedEvent { createdAt } } } "
            "commits(last: 1) { nodes { commit { committedDate status { contexts { "
            "context state targetUrl } } } } } } } }",
        )
    res = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={{ viewer {{ login }} {' '.join(queries)} }}"],
        capture_output=True,
        check=False,
        text=True,
    )
    if res.returncode:
        raise ValueError(res.stderr.strip() or "gh failed")
    data = json.loads(res.stdout)["data"]
    prs = {
        pair: nodes[0]
        for i, pair in enumerate(pairs)
        if (nodes := data[f"p{i}"]["pullRequests"]["nodes"])
    }
    for pr in prs.values():
        pr["asked"], pr["auto_asked"] = get_asks(pr, data["viewer"]["login"])
        pr["viewer"] = data["viewer"]["login"]
        pr["pushed"] = get_last_push(pr)
    return prs


def get_prs(pairs):
    chunks = [pairs[i : i + PRS_PER_QUERY] for i in range(0, len(pairs), PRS_PER_QUERY)]
    try:
        with ThreadPoolExecutor(max_workers=max(len(chunks), 1)) as executor:
            found = list(executor.map(read_prs, chunks))
    except ValueError as error:
        return None, [f"GitHub: {error}"]
    prs = {pair: pr for chunk in found for pair, pr in chunk.items()}
    errors = set()
    with ThreadPoolExecutor(max_workers=max(len(prs), 1)) as executor:
        for pr, (state, error) in zip(prs.values(), executor.map(get_mergebot_state, prs.values())):
            pr["mergebot"] = state
            if error:
                errors.add(f"mergebot: {error}")
    return prs, sorted(errors)


def get_asks(pr, login):
    mine, automated = [], [pr["createdAt"]]
    thread_comments = [t["comments"]["nodes"][0] for t in pr["reviewThreads"]["nodes"]]
    for node in pr["comments"]["nodes"] + pr["timelineItems"]["nodes"] + thread_comments:
        by_me = (node.get("author") or node.get("actor") or {}).get("login") == login
        (mine if by_me else automated).append(node["createdAt"])
    return [
        datetime.fromisoformat(max(dates)).timestamp() if dates else None
        for dates in (mine, automated)
    ]


def get_ask(pr, now):
    if pr["asked"] is not None and pr["pushed"] > pr["asked"]:
        return "yellow", "not asked since push", pr["pushed"]
    if pr["asked"] is None:
        styles, label, date = AUTO_ASKED_STYLES, "auto-requested", pr["auto_asked"]
    else:
        styles, label, date = ASKED_STYLES, "asked", pr["asked"]
    return next(style for limit, style in styles if now - date >= limit), label, date


def get_last_push(pr):
    dates = [node["createdAt"] for node in pr["forcePushes"]["nodes"]]
    dates += [node["commit"]["committedDate"] for node in pr["commits"]["nodes"]]
    return max(datetime.fromisoformat(date).timestamp() for date in dates)


def get_mergebot_state(pr):
    url = pr["url"].replace("https://github.com", "https://mergebot.odoo.com") + ".json"
    try:
        with urllib.request.urlopen(url, timeout=MERGEBOT_TIMEOUT) as res:
            return json.load(res)["state"], None
    except urllib.error.HTTPError as error:
        return None, None if error.code == 404 else str(error)
    except OSError as error:
        return None, str(getattr(error, "reason", error))


def get_push(repo, branch):
    remote_ref = get_remote_dev_ref(branch, repo)
    if git(repo, "rev-parse", "--verify", "--quiet", remote_ref) is None:
        return None
    counts = git(repo, "rev-list", "--left-right", "--count", f"{branch}...{remote_ref}")
    ahead, behind = counts.split()
    return {"ahead": int(ahead), "behind": int(behind)}


def format_push(push):
    if push is None:
        return "[dim]no remote[/dim]"
    return " ".join(
        [f"[yellow]+{push['ahead']}[/yellow]"] * bool(push["ahead"])
        + [f"[yellow]-{push['behind']}[/yellow]"] * bool(push["behind"]),
    )


def has_conflict(repo, branch, base_ref, write_tree):
    if write_tree:
        res = subprocess.run(
            ["git", "merge-tree", "--write-tree", "--name-only", base_ref, branch],
            capture_output=True,
            check=False,
            cwd=get_repo_folder(repo),
        )
        return res.returncode == 1
    merge_base = git(repo, "merge-base", base_ref, branch)
    out = git(repo, "merge-tree", merge_base, base_ref, branch)
    return out is not None and re.search(r"^\+<{7} ", out, re.MULTILINE) is not None


def get_status(repo, branch, write_tree):
    base_ref = get_remote_ref(get_base_for_repo(get_base_from_bundle_name(branch), repo), repo)
    if git(repo, "rev-parse", "--verify", "--quiet", base_ref) is None:
        return None
    ahead, behind = map(
        int,
        git(repo, "rev-list", "--left-right", "--count", f"{branch}...{base_ref}").split(),
    )
    return {
        "ahead": ahead,
        "behind": behind,
        "conflict": behind > 0 and has_conflict(repo, branch, base_ref, write_tree),
    }


def format_age(seconds, plain=False):
    age = next(
        (f"{int(seconds // size)}{unit}" for unit, size in AGE_UNITS if seconds >= size),
        "now",
    )
    if plain:
        return age
    style = "default" if seconds < 7 * 86400 else "dim"
    return f"[{style}]{age}[/{style}]"


def dim_markup(markup):
    text = Text.from_markup(markup)
    dimmed = Text(text.plain, style="dim")
    for span in text.spans:
        style = span.style if isinstance(span.style, Style) else Style.parse(span.style)
        if style.link:
            dimmed.stylize(Style(link=style.link), span.start, span.end)
    return dimmed


def get_batch(context):
    match = re.search(r"/batch/(\d+)/", context["targetUrl"] or "")
    return int(match[1]) if match else None


def get_pr_facts(pr, repo, now):
    state = "draft" if pr["isDraft"] and pr["state"] == "OPEN" else pr["state"].lower()
    if pr["mergebot"] == "merged":
        state = "merged"
    bodies = [node["body"] for key in ("comments", "reviews") for node in pr[key]["nodes"]]
    contexts = [
        c
        for c in (pr["commits"]["nodes"][0]["commit"]["status"] or {}).get("contexts", [])
        if c["context"] != "ci/codeowner"
    ]
    batches = {c["context"]: get_batch(c) for c in contexts}
    latest = max(filter(None, batches.values()), default=None)
    running = any(c["state"] == "PENDING" for c in contexts)
    stale = {
        c["context"]
        for c in contexts
        if running
        and c["state"] in ("ERROR", "FAILURE")
        and batches[c["context"]] not in (None, latest)
    }
    main_check = MAIN_CHECK_BY_REPO.get(repo)
    ci_not_started = main_check and not any(
        c["context"] in (main_check, f"{main_check} (light)") for c in contexts
    )
    open_threads = [t for t in pr["reviewThreads"]["nodes"] if not t["isResolved"]]
    to_answer = [
        t
        for t in open_threads
        if (t["comments"]["nodes"][0]["author"] or {}).get("login") != pr["viewer"]
    ]
    return {
        "ask": dict(zip(("style", "label", "date"), get_ask(pr, now))),
        "delegated": any(DELEGATE.search(body) for body in bodies),
        "failing": [
            {"context": c["context"], "url": c["targetUrl"]}
            for c in contexts
            if c["state"] in ("ERROR", "FAILURE") and c["context"] not in stale
        ],
        "mergebot": pr["mergebot"],
        "number": pr["number"],
        "open": pr["state"] == "OPEN",
        "pending": bool(ci_not_started or running),
        "replied": len(open_threads) - len(to_answer),
        "review": pr["reviewDecision"],
        "state": state,
        "threads": len(to_answer),
        "url": pr["url"],
    }


def format_pr(facts):
    if not facts:
        return "", ""
    state = facts["state"]
    style = PR_STYLES.get(state)
    tags = [f"[{style}]{state}[/{style}]"] if style and state != "draft" else []
    if facts["open"]:
        if mergebot := MERGEBOT_TAGS.get(facts["mergebot"]):
            tags.append(mergebot)
        elif facts["delegated"]:
            tags.append("[green]delegated[/green]")
        elif review := REVIEW_TAGS.get(facts["review"]):
            tags.append(review)
        if threads := facts["threads"]:
            tags.append(f"[yellow]{threads} thread{'s' * (threads > 1)}[/yellow]")
        if replied := facts["replied"]:
            tags.append(f"[dim]{replied} replied[/dim]")
        tags += [
            f"[{MINOR_CHECKS.get(c['context'], 'red')}]"
            f"[link={c['url']}]{c['context'].removeprefix('ci/')}[/link][/]"
            for c in facts["failing"]
        ]
        if facts["pending"]:
            tags.append(CI_RUNNING)
    number = f"[link={facts['url']}]{facts['number']}[/link]"
    return f"[{style}]{number}[/{style}]" if style else number, " ".join(tags)


def format_ask(ask, now):
    age = format_age(now - ask["date"], plain=True)
    return f"[{ask['style']}]{ask['label']} {age}[/{ask['style']}]"


def get_group(facts, status, push):
    if not facts:
        return "me", "wip"
    if facts["state"] in ("closed", "merged") or facts["mergebot"] in ("ready", "staged"):
        return "mergebot", None
    if (
        (status and status["conflict"])
        or any(c["context"] not in MINOR_CHECKS for c in facts["failing"])
        or facts["mergebot"] == "error"
    ):
        return "me", "red"
    unpushed = push is None or any(push.values())
    if unpushed or any(c["context"] == "ci/style" for c in facts["failing"]):
        return "me", "wip"
    if (
        facts["threads"]
        or facts["review"] == "CHANGES_REQUESTED"
        or (facts["delegated"] and facts["mergebot"] not in R_PLUS_STATES)
    ):
        return "me", "review"
    if facts["pending"] or facts["mergebot"] == "approved":
        return "ci", None
    if facts["state"] == "draft":
        return "drafts", None
    return "reviewer", None


def get_bundle_agent(bundle, state, is_open, now):
    queue = [item["bundle"] for item in state["queue"]]
    if bundle in queue:
        return {"queued": queue.index(bundle) + 1, "state": "queued"}
    if bundle in state.get("building", []):
        return {"state": "building"}
    alive = is_open or now - state["launches"].get(bundle, 0) < LAUNCH_GRACE
    return get_agent(bundle, alive=alive)


def format_agent(agent, group):
    if not agent or (agent["state"] == "stopped" and group != "me"):
        return ""
    return AGENT_TAGS.get(agent["state"], "").format(**agent)


def get_compare_url(repo, branch):
    base = get_base_for_repo(get_base_from_bundle_name(branch), repo)
    owner = get_github_repo(repo, get_remote_dev_repo(repo)).split("/")[0]
    return f"https://github.com/{get_github_repo(repo)}/compare/{base}...{owner}:{branch}?expand=1"


def get_bundles():
    repos = [repo for repo in get_repos() if os.path.isdir(get_repo_folder(repo))]
    write_tree = has_write_tree()
    bundles = {}
    heads = {}
    worktree_branches = set()
    with ThreadPoolExecutor() as executor:
        for repo, branches in zip(repos, executor.map(get_local_branches, repos)):
            for branch, date, head, in_worktree in branches:
                if branch not in get_sticky_bundles(repo):
                    bundles.setdefault(branch, {})[repo] = date
                    heads[repo, branch] = head
                    if in_worktree:
                        worktree_branches.add(branch)
        pairs = [(repo, branch) for branch, dates in bundles.items() for repo in dates]
        prs_future = executor.submit(get_prs, pairs)
        open_future = executor.submit(get_open_bundle_folders)
        statuses = dict(
            zip(pairs, executor.map(lambda pair: get_status(*pair, write_tree), pairs)),
        )
        pushes = dict(zip(pairs, executor.map(lambda pair: get_push(*pair), pairs)))
        prs, errors = prs_future.result()
        open_folders = open_future.result()

    agent_state = read_state()
    groups = {group: [] for group in GROUPS}
    now = time.time()
    for branch, dates in sorted(bundles.items(), key=lambda item: -max(item[1].values())):
        repos_of_bundle = {}
        delegated = False
        for repo in sorted(dates, key=lambda repo: repo != "odoo"):
            pr = (prs or {}).get((repo, branch))
            facts = get_pr_facts(pr, repo, now) if pr else None
            status = statuses[repo, branch]
            push = pushes[repo, branch]
            delegated |= (
                bool(facts) and facts["delegated"] and facts["mergebot"] not in R_PLUS_STATES
            )
            group, issue = (
                get_group(facts, status, push) if BUNDLE_SUFFIX in branch else ("others", None)
            )
            empty = (
                not facts and bool(status) and not status["ahead"] and not (push and push["behind"])
            )
            pushed = push is not None and not any(push.values())
            create_pr = prs is not None and group == "me" and not facts and not empty and pushed
            repos_of_bundle[repo] = {
                "ahead": status and status["ahead"],
                "behind": status and status["behind"],
                "conflict": status and status["conflict"],
                "create_pr": get_compare_url(repo, branch) if create_pr else None,
                "empty": empty,
                "group": group,
                "head": heads[repo, branch],
                "issue": issue,
                "pr": facts,
                "push": push,
            }
        rows = [row for row in repos_of_bundle.values() if not row["empty"]]
        rows = rows or list(repos_of_bundle.values())
        group = min((row["group"] for row in rows), key=GROUP_PRECEDENCE.index)
        issues = {row["issue"] for row in rows if row["group"] == group}
        issue = max(issues - {None}, key=list(ISSUE_RANKS).index, default=None)
        is_open = get_worktree_bundle_folder(branch) in open_folders
        agent = get_bundle_agent(branch, agent_state, is_open, now)
        if group == "me" and agent and agent["state"] in AGENT_WORKING:
            group, issue = "agents", None
        bundle = {
            "agent": agent,
            "bundle": branch,
            "date": max(dates.values()),
            "delegated": delegated,
            "group": group,
            "issue": issue,
            "open": is_open,
            "repos": repos_of_bundle,
            "worktree": branch in worktree_branches,
        }
        urgency, date = 0 if is_open else ISSUE_RANKS.get(issue, 0), bundle["date"]
        if group == "reviewer" and not is_open:
            asks = [row["pr"]["ask"] for row in repos_of_bundle.values() if row["pr"]]
            urgency = min(ASK_SEVERITIES[ask["style"]] for ask in asks)
            date = min(ask["date"] for ask in asks if ASK_SEVERITIES[ask["style"]] == urgency)
        groups["open" if is_open else group].append(((not delegated, urgency, date), bundle))
    ordered = [
        bundle
        for bundles_of_group in groups.values()
        for _key, bundle in sorted(bundles_of_group, key=lambda item: item[0])
    ]
    return ordered, errors


def get_rows(bundle, group, now):
    branch = bundle["bundle"]
    age = format_age(now - bundle["date"])
    label = f"[cyan]{branch}[/cyan]" if bundle["worktree"] else branch
    icon = "\N{OPEN FILE FOLDER}" if bundle["worktree"] else "\N{INBOX TRAY}"
    opener = f"[link=odoo-bundle://{branch}]{icon}[/link]"
    agent = format_agent(bundle["agent"], bundle["group"])
    rows = []
    for repo, row in bundle["repos"].items():
        facts = row["pr"]
        push = format_push(row["push"])
        if facts and facts["state"] == "merged":
            behind = conflict = push = ""
        elif row["behind"] is None:
            behind = conflict = "-"
        else:
            style = next(style for limit, style in BEHIND_STYLES if row["behind"] >= limit)
            behind = f"[{style}]{row['behind']}[/{style}]"
            conflict = "[red]yes[/red]" if row["conflict"] else ""
        number, tags = format_pr(facts)
        if row["empty"]:
            tags = "[dim]no commit[/dim]"
        elif row["create_pr"] and bundle["group"] == "me":
            tags = f"[yellow][link={row['create_pr']}]create PR[/link][/yellow]"
        if group == "ci":
            tags = tags.removesuffix(CI_RUNNING).rstrip()
        if group == "reviewer" and row["group"] == "reviewer":
            tags = f"{format_ask(facts['ask'], now)} {tags}".rstrip()
        tags = f"{agent} {tags}".strip()
        if row["group"] == "others":
            tags = dim_markup(tags)
        rows.append((age, opener, label, repo, push, behind, conflict, number, tags))
        age = agent = opener = label = ""
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print the bundles as JSON")
    args = parser.parse_args()
    if args.json:
        bundles, errors = get_bundles()
        print(json.dumps({"bundles": bundles, "errors": errors}))
        return

    console = Console()
    with console.status("Reading branches and PRs"):
        bundles, errors = get_bundles()
    now = time.time()
    groups = {group: [] for group in GROUPS}
    counts = dict.fromkeys(GROUPS, 0)
    for bundle in bundles:
        group = "open" if bundle["open"] else bundle["group"]
        groups[group] += get_rows(bundle, group, now)
        counts[group] += 1
    rows = [row for group_rows in groups.values() for row in group_rows]
    table = Table(box=None, header_style="bold")
    for i, column in enumerate(("age", "", "branch", "repo", "push", "behind", "conflict", "PR")):
        table.add_column(
            column,
            justify="right" if column in ("age", "behind", "PR") else "left",
            min_width=max(
                Text.from_markup(cell).cell_len for cell in (column, *(row[i] for row in rows))
            ),
            no_wrap=True,
        )
    table.add_column("state", min_width=10)
    for group, group_rows in groups.items():
        if group_rows:
            if table.row_count:
                table.add_row()
            table.add_row("", "", f"[bold]{GROUPS[group]}[/bold] [dim]{counts[group]}[/dim]")
            for row in group_rows:
                table.add_row(*row)
    console.print(table)
    for error in errors:
        console.print(f"[yellow]PR state incomplete[/yellow], {error}")


if __name__ == "__main__":
    main()
