"""List local bundle branches like `git branch`, with what is not pushed, how far behind their base
they are, whether a rebase would conflict, and their PR with its review and CI state.

Reads local refs only: run `gfa` first for fresh numbers.

Examples:
 $ python ~/repo/useful-things/scripts/branch_status.py
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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

from commands import (
    BUNDLE_SUFFIX,
    get_base_for_repo,
    get_base_from_bundle_name,
    get_remote_dev_ref,
    get_remote_ref,
    get_remote_repo,
    get_repo_folder,
    get_repos,
    get_sticky_bundles,
    get_worktree_bundle_folder,
)

AGE_UNITS = (("d", 86400), ("h", 3600), ("m", 60))
ASK_SEVERITIES = {"dim": 2, "red": 0, "yellow": 1}
ASKED_STYLES = ((7 * 86400, "red"), (2 * 86400, "yellow"), (0, "dim"))
AUTO_ASKED_STYLES = ((7 * 86400, "red"), (86400, "yellow"), (0, "dim"))
BEHIND_STYLES = ((501, "red"), (50, "yellow"), (0, "default"))
CI_RUNNING = "[dim]ci running[/dim]"
DELEGATE = re.compile(r"@robodoo\b.*\bdelegate[+=]")
GROUPS = {
    "open": "Open in VS Code",
    "me": "Waits on me",
    "drafts": "Drafts",
    "reviewer": "Waits on a reviewer",
    "ci": "Waits on CI",
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
        "--format=%(refname:short) %(committerdate:unix) %(worktreepath)",
        "refs/heads/",
    )
    return [
        (name, int(date), any(path))
        for name, date, *path in (line.split(" ", 2) for line in out.splitlines())
    ]


def get_github_repo(repo):
    url = git(repo, "remote", "get-url", get_remote_repo(repo))
    return re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)[1]


def get_prs(pairs):
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
        return {}, [f"GitHub: {res.stderr.strip() or 'gh failed'}"]
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
        return "[dim]no remote[/dim]", True
    counts = git(repo, "rev-list", "--left-right", "--count", f"{branch}...{remote_ref}")
    ahead, behind = counts.split()
    markup = " ".join(
        [f"[yellow]+{ahead}[/yellow]"] * (ahead != "0")
        + [f"[yellow]-{behind}[/yellow]"] * (behind != "0"),
    )
    return markup, bool(markup)


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
    behind = int(git(repo, "rev-list", "--count", f"{branch}..{base_ref}"))
    return {
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


def get_pr_facts(pr, repo):
    state = "draft" if pr["isDraft"] and pr["state"] == "OPEN" else pr["state"].lower()
    if pr["mergebot"] == "merged":
        state = "merged"
    bodies = [node["body"] for key in ("comments", "reviews") for node in pr[key]["nodes"]]
    contexts = (pr["commits"]["nodes"][0]["commit"]["status"] or {}).get("contexts", [])
    batches = {c["context"]: get_batch(c) for c in contexts}
    latest = max(filter(None, batches.values()), default=None)
    stale = {
        c["context"]
        for c in contexts
        if c["state"] in ("ERROR", "FAILURE") and batches[c["context"]] not in (None, latest)
    }
    main_check = MAIN_CHECK_BY_REPO.get(repo)
    ci_not_started = main_check and main_check not in {c["context"] for c in contexts}
    open_threads = [t for t in pr["reviewThreads"]["nodes"] if not t["isResolved"]]
    to_answer = [
        t
        for t in open_threads
        if (t["comments"]["nodes"][0]["author"] or {}).get("login") != pr["viewer"]
    ]
    return {
        "delegated": any(DELEGATE.search(body) for body in bodies),
        "failing": [
            c
            for c in contexts
            if c["state"] in ("ERROR", "FAILURE")
            and c["context"] != "ci/codeowner"
            and c["context"] not in stale
        ],
        "pending": ci_not_started or bool(stale) or any(c["state"] == "PENDING" for c in contexts),
        "state": state,
        "replied": len(open_threads) - len(to_answer),
        "threads": len(to_answer),
    }


def format_pr(pr, facts):
    if not pr:
        return "", ""
    state = facts["state"]
    style = PR_STYLES.get(state)
    tags = [f"[{style}]{state}[/{style}]"] if style and state != "draft" else []
    if pr["state"] == "OPEN":
        if mergebot := MERGEBOT_TAGS.get(pr["mergebot"]):
            tags.append(mergebot)
        elif facts["delegated"]:
            tags.append("[green]delegated[/green]")
        elif review := REVIEW_TAGS.get(pr["reviewDecision"]):
            tags.append(review)
        if threads := facts["threads"]:
            tags.append(f"[yellow]{threads} thread{'s' * (threads > 1)}[/yellow]")
        if replied := facts["replied"]:
            tags.append(f"[dim]{replied} replied[/dim]")
        tags += [
            f"[{MINOR_CHECKS.get(c['context'], 'red')}]"
            f"[link={c['targetUrl']}]{c['context'].removeprefix('ci/')}[/link][/]"
            for c in facts["failing"]
        ]
        if facts["pending"]:
            tags.append(CI_RUNNING)
    number = f"[link={pr['url']}]{pr['number']}[/link]"
    return f"[{style}]{number}[/{style}]" if style else number, " ".join(tags)


def format_ask(pr, now):
    style, label, date = get_ask(pr, now)
    return f"[{style}]{label} {format_age(now - date, plain=True)}[/{style}]"


def get_group(pr, facts, status, unpushed):
    if not pr:
        return "me", "wip"
    if facts["state"] in ("closed", "merged") or pr["mergebot"] in ("ready", "staged"):
        return "mergebot", None
    if (
        (status and status["conflict"])
        or any(c["context"] not in MINOR_CHECKS for c in facts["failing"])
        or pr["mergebot"] == "error"
    ):
        return "me", "red"
    if unpushed or any(c["context"] == "ci/style" for c in facts["failing"]):
        return "me", "wip"
    if (
        facts["threads"]
        or pr["reviewDecision"] == "CHANGES_REQUESTED"
        or (facts["delegated"] and pr["mergebot"] not in R_PLUS_STATES)
    ):
        return "me", "review"
    if facts["pending"] or pr["mergebot"] == "approved":
        return "ci", None
    if facts["state"] == "draft":
        return "drafts", None
    return "reviewer", None


def main():
    repos = [repo for repo in get_repos() if os.path.isdir(get_repo_folder(repo))]
    write_tree = has_write_tree()
    bundles = {}
    worktree_branches = set()
    console = Console()
    with console.status("Reading branches and PRs"), ThreadPoolExecutor() as executor:
        for repo, branches in zip(repos, executor.map(get_local_branches, repos)):
            for branch, date, in_worktree in branches:
                if branch not in get_sticky_bundles(repo):
                    bundles.setdefault(branch, {})[repo] = date
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

    groups = {group: [] for group in GROUPS}
    now = time.time()
    for branch, dates in sorted(bundles.items(), key=lambda item: -max(item[1].values())):
        age = format_age(now - max(dates.values()))
        label = f"[cyan]{branch}[/cyan]" if branch in worktree_branches else branch
        icon = "\N{OPEN FILE FOLDER}" if branch in worktree_branches else "\N{INBOX TRAY}"
        opener = f"[link=odoo-bundle://{branch}]{icon}[/link]"
        rows = []
        bundle_groups = []
        ask_tags = {}
        delegated = False
        for repo in sorted(dates, key=lambda repo: repo != "odoo"):
            status = statuses[repo, branch]
            if status is None:
                behind = conflict = "-"
            else:
                style = next(style for limit, style in BEHIND_STYLES if status["behind"] >= limit)
                behind = f"[{style}]{status['behind']}[/{style}]"
                conflict = "[red]yes[/red]" if status["conflict"] else ""
            pr = prs.get((repo, branch))
            facts = get_pr_facts(pr, repo) if pr else {}
            number, tags = format_pr(pr, facts)
            delegated |= bool(facts) and facts["delegated"] and pr["mergebot"] not in R_PLUS_STATES
            push, unpushed = pushes[repo, branch]
            if BUNDLE_SUFFIX in branch:
                bundle_groups.append(get_group(pr, facts, status, unpushed))
                if bundle_groups[-1][0] == "reviewer":
                    ask_tags[len(rows)] = format_ask(pr, now)
            else:
                bundle_groups.append(("others", None))
                tags = dim_markup(tags)
            rows.append((age, opener, label, repo, push, behind, conflict, number, tags))
            age = opener = label = ""
        group = min((group for group, _kind in bundle_groups), key=GROUP_PRECEDENCE.index)
        if get_worktree_bundle_folder(branch) in open_folders:
            group = "open"
        kinds = {kind for row_group, kind in bundle_groups if row_group == group and kind}
        worst = max(kinds, key=list(ISSUE_RANKS).index, default=None)
        urgency = ISSUE_RANKS.get(worst, 0)
        if group == "ci":
            rows = [(*row[:-1], row[-1].removesuffix(CI_RUNNING).rstrip()) for row in rows]
        if group == "reviewer":
            for i, ask_tag in ask_tags.items():
                rows[i] = (*rows[i][:-1], f"{ask_tag} {rows[i][-1]}".rstrip())
            waiting = [prs[repo, branch] for repo in dates if (repo, branch) in prs]
            asks = [get_ask(pr, now) for pr in waiting]
            urgency = min(ASK_SEVERITIES[style] for style, _label, _date in asks)
            date = min(date for style, _label, date in asks if ASK_SEVERITIES[style] == urgency)
        else:
            date = max(dates.values())
        groups[group].append((not delegated, urgency, date, rows))

    counts = {group: len(bundles_of_group) for group, bundles_of_group in groups.items()}
    for group, bundles_of_group in groups.items():
        bundles_of_group.sort(key=lambda bundle: bundle[:3])
        groups[group] = [row for bundle in bundles_of_group for row in bundle[3]]
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
