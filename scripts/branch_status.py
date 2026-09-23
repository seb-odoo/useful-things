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
)

AGE_UNITS = (("d", 86400), ("h", 3600), ("m", 60))
ASKED_STYLES = ((7 * 86400, "red"), (2 * 86400, "yellow"), (0, "dim"))
BEHIND_STYLES = ((501, "red"), (50, "yellow"), (0, "default"))
DELEGATE = re.compile(r"@robodoo\b.*\bdelegate[+=]")
GROUPS = {
    "me": "Waits on me",
    "drafts": "Drafts",
    "reviewer": "Waits on a reviewer",
    "ci": "Waits on CI",
    "mergebot": "Waits on mergebot",
    "others": "Not mine",
}
MERGEBOT_TAGS = {
    "approved": "r+",
    "error": "[red]staging error[/red]",
    "ready": "[green]r+ ready[/green]",
    "staged": "[green]staged[/green]",
}
MERGEBOT_TIMEOUT = 3
MINOR_CHECKS = {"ci/security": "dim", "ci/style": "yellow"}
PR_STYLES = {"closed": "red", "draft": "dim", "merged": "magenta"}
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
            "reviewThreads(first: 100) { nodes { isResolved } } "
            "commits(last: 1) { nodes { commit { status { contexts { "
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
        pr["asked"] = get_last_ask(pr, data["viewer"]["login"])
    errors = set()
    with ThreadPoolExecutor(max_workers=max(len(prs), 1)) as executor:
        for pr, (state, error) in zip(prs.values(), executor.map(get_mergebot_state, prs.values())):
            pr["mergebot"] = state
            if error:
                errors.add(f"mergebot: {error}")
    return prs, sorted(errors)


def get_last_ask(pr, login):
    dates = [
        node["createdAt"]
        for node in pr["comments"]["nodes"] + pr["timelineItems"]["nodes"]
        if (node.get("author") or node.get("actor") or {}).get("login") == login
    ]
    return datetime.fromisoformat(max(dates, default=pr["createdAt"])).timestamp()


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


def get_pr_facts(pr):
    state = "draft" if pr["isDraft"] and pr["state"] == "OPEN" else pr["state"].lower()
    if pr["mergebot"] == "merged":
        state = "merged"
    bodies = [node["body"] for key in ("comments", "reviews") for node in pr[key]["nodes"]]
    contexts = (pr["commits"]["nodes"][0]["commit"]["status"] or {}).get("contexts", [])
    return {
        "delegated": any(DELEGATE.search(body) for body in bodies),
        "failing": [
            c
            for c in contexts
            if c["state"] in ("ERROR", "FAILURE") and c["context"] != "ci/codeowner"
        ],
        "pending": any(c["state"] == "PENDING" for c in contexts),
        "state": state,
        "threads": sum(not t["isResolved"] for t in pr["reviewThreads"]["nodes"]),
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
        tags += [
            f"[{MINOR_CHECKS.get(c['context'], 'red')}]"
            f"[link={c['targetUrl']}]{c['context'].removeprefix('ci/')}[/link][/]"
            for c in facts["failing"]
        ]
        if facts["pending"]:
            tags.append("[dim]ci running[/dim]")
    number = f"[link={pr['url']}]{pr['number']}[/link]"
    return f"[{style}]{number}[/{style}]" if style else number, " ".join(tags)


def get_group(pr, facts, status, unpushed):
    if not pr:
        return "me", 1
    if facts["state"] in ("closed", "merged") or pr["mergebot"] in ("ready", "staged"):
        return "mergebot", 2
    if (
        (status and status["conflict"])
        or any(c["context"] not in MINOR_CHECKS for c in facts["failing"])
        or pr["reviewDecision"] == "CHANGES_REQUESTED"
        or pr["mergebot"] == "error"
    ):
        return "me", 0
    if (
        unpushed
        or facts["threads"]
        or (facts["delegated"] and pr["mergebot"] != "approved")
        or any(c["context"] == "ci/style" for c in facts["failing"])
    ):
        return "me", 1
    if facts["pending"] or pr["mergebot"] == "approved":
        return "ci", 2
    if facts["state"] == "draft":
        return "drafts", 2
    return "reviewer", 2


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
        statuses = dict(
            zip(pairs, executor.map(lambda pair: get_status(*pair, write_tree), pairs)),
        )
        pushes = dict(zip(pairs, executor.map(lambda pair: get_push(*pair), pairs)))
        prs, errors = prs_future.result()

    groups = {group: [] for group in GROUPS}
    now = time.time()
    for branch, dates in sorted(bundles.items(), key=lambda item: -max(item[1].values())):
        age = format_age(now - max(dates.values()))
        label = f"[cyan]{branch}[/cyan]" if branch in worktree_branches else branch
        icon = "\N{OPEN FILE FOLDER}" if branch in worktree_branches else "\N{INBOX TRAY}"
        opener = f"[link=odoo-bundle://{branch}]{icon}[/link]"
        rows = []
        bundle_groups = []
        for repo in sorted(dates, key=lambda repo: repo != "odoo"):
            status = statuses[repo, branch]
            if status is None:
                behind = conflict = "-"
            else:
                style = next(style for limit, style in BEHIND_STYLES if status["behind"] >= limit)
                behind = f"[{style}]{status['behind']}[/{style}]"
                conflict = "[red]yes[/red]" if status["conflict"] else ""
            pr = prs.get((repo, branch))
            facts = get_pr_facts(pr) if pr else {}
            number, tags = format_pr(pr, facts)
            push, unpushed = pushes[repo, branch]
            if BUNDLE_SUFFIX in branch:
                bundle_groups.append(get_group(pr, facts, status, unpushed))
                if bundle_groups[-1][0] == "reviewer":
                    waited = now - pr["asked"]
                    style = next(style for limit, style in ASKED_STYLES if waited >= limit)
                    tags = f"{tags} [{style}]asked {format_age(waited, plain=True)}[/{style}]"
            else:
                bundle_groups.append(("others", 2))
                tags = dim_markup(tags)
            rows.append((age, opener, label, repo, push, behind, conflict, number, tags))
            age = opener = label = ""
        group, urgency = min(bundle_groups, key=lambda item: (list(GROUPS).index(item[0]), item[1]))
        if group == "reviewer":
            date = max(prs[repo, branch]["asked"] for repo in dates if (repo, branch) in prs)
        else:
            date = max(dates.values())
        groups[group].append((urgency, date, rows))

    for group, bundles_of_group in groups.items():
        bundles_of_group.sort(key=lambda bundle: bundle[:2])
        groups[group] = [row for bundle in bundles_of_group for row in bundle[2]]
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
            table.add_row("", "", f"[bold]{GROUPS[group]}[/bold]")
            for row in group_rows:
                table.add_row(*row)
    console.print(table)
    for error in errors:
        console.print(f"[yellow]PR state incomplete[/yellow], {error}")


if __name__ == "__main__":
    main()
