"""List local bundle branches like `git branch`, with what is not pushed, how far behind their base
they are, whether a rebase would conflict, and their PR with its review and CI state.

Reads local refs only: run `gfa` first for fresh numbers.

Examples:
 $ python ~/repo/useful-things/scripts/branch_status.py
 $ python ~/repo/useful-things/scripts/branch_status.py --json
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
from contextlib import redirect_stdout
from datetime import datetime
import functools
import io
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.request

from rich.console import Console
from rich.live import Live
from rich.style import Style
from rich.table import Table
from rich.text import Text

from agents import LAUNCH_GRACE, STATE, get_agent, is_handled, read_state
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
from delete_bundle import delete_bundles, get_unsaved_work
from utils import UtilsRunner

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
BOTS = ("fw-bot", "robodoo")
CI_RUNNING = "[dim]ci running[/dim]"
COMMITS_READ = 50
DELEGATE = re.compile(r"@robodoo\b.*\bdelegate[+=]")
FW_BOT = "fw-bot"
# A bundle writes the .git/config of these repos: git runs none of the programs it may name there.
GIT = ("git", "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null", "-c", "core.pager=cat")
GIT_CACHE = STATE / "branch-status.json"
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
LEADING_PINGS = re.compile(r"^(?:\s*@[\w-]+)+")
MAIL_AGENT = pathlib.Path.home() / ".claude" / "external" / "mail-agent"
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
MENTION = re.compile(r"(?<![\w/])@([\w-]+)(?![\w/-])")
MERGEBOT_TIMEOUT = 3
MINOR_CHECKS = {"ci/security": "dim", "ci/style": "yellow"}
ORG_MEMBERS = ("MEMBER", "OWNER")
OVERRIDE = re.compile(r"@robodoo\b.*\boverride=([\w/.,-]+)")
PR_STYLES = {"closed": "red", "draft": "dim", "merged": "magenta"}
PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
PRS_PER_QUERY = 5
R_PLUS_STATES = ("approved", "merged", "ready", "staged")
REVIEW_TAGS = {"APPROVED": "approved", "CHANGES_REQUESTED": "[red]changes[/red]"}


def git(repo, *args):
    res = subprocess.run(
        [*GIT, *args],
        capture_output=True,
        check=False,
        cwd=get_repo_folder(repo),
        errors="replace",
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


def get_subjects(repo):
    """{branch: the subject of its tip}, read here so that the ops container runs no git in a repo
    the bundles write (triage)."""
    out = git(repo, "for-each-ref", "--format=%(refname:short)%00%(subject)", "refs/heads/")
    return dict(line.split("\0", 1) for line in out.splitlines())


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
            "{ nodes { author { login } createdAt headRefName headRefOid isDraft number "
            "reviewDecision state url "
            "comments(last: 30) { nodes { author { login } authorAssociation body createdAt } } "
            "reviews(last: 30) { nodes { author { login } authorAssociation body submittedAt } } "
            "timelineItems(last: 20, itemTypes: [REVIEW_REQUESTED_EVENT]) { nodes { "
            "... on ReviewRequestedEvent { actor { login } createdAt "
            "requestedReviewer { ... on User { login } } } } } "
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
            [*GIT, "merge-tree", "--write-tree", "--name-only", base_ref, branch],
            capture_output=True,
            check=False,
            cwd=get_repo_folder(repo),
        )
        return res.returncode == 1
    merge_base = git(repo, "merge-base", base_ref, branch)
    out = git(repo, "merge-tree", merge_base, base_ref, branch)
    return out is not None and re.search(r"^\+<{7} ", out, re.MULTILINE) is not None


def get_base_ref(repo, branch):
    return get_remote_ref(get_base_for_repo(get_base_from_bundle_name(branch), repo), repo)


def get_status(repo, branch, write_tree):
    base_ref = get_base_ref(repo, branch)
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


def get_commits(repo, branch):
    """The commits of the branch over its base, newest first, with their subject and patch-id, for
    the restack of triage; None past COMMITS_READ. Plumbing only: `git log -p` and `git show` run
    the textconv a bundle names in the config."""
    out = git(
        repo,
        "rev-list",
        f"--max-count={COMMITS_READ + 1}",
        "--no-commit-header",
        "--format=%H %s",
        f"{get_base_ref(repo, branch)}..{branch}",
    )
    if out is None:
        return None
    commits = [line.partition(" ")[::2] for line in out.splitlines()]
    if not commits or len(commits) > COMMITS_READ:
        return None if commits else []
    shas = "".join(f"{sha}\n" for sha, _subject in commits).encode()
    folder = get_repo_folder(repo)
    diff = subprocess.run(
        [*GIT, "diff-tree", "--stdin", "-p"],
        capture_output=True,
        check=True,
        cwd=folder,
        input=shas,
    ).stdout
    ids = subprocess.run(
        [*GIT, "patch-id", "--stable"], capture_output=True, check=True, cwd=folder, input=diff
    ).stdout.decode()
    patch_ids = {sha: patch_id for patch_id, sha in map(str.split, ids.splitlines())}
    return [
        {"patch_id": patch_ids.get(sha), "sha": sha, "subject": subject} for sha, subject in commits
    ]


def read_heads(repo, refs):
    """{ref: its commit} of these refs of repo, from one git call: a missing ref is left out."""
    out = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", *refs) if refs else ""
    return dict(line.split(" ") for line in (out or "").splitlines())


def read_git_facts(executor, heads, write_tree):
    """The status, the push and the commits of each (repo, branch) of heads, which maps them to
    their commit. On the host each git call starts a host-box, 20 to 40 ms of CPU, and triage
    reads gbs every 10 minutes: GIT_CACHE keeps them by the commits of the branch, of its base
    and of its dev branch, and git reads them again only once one of those moved."""
    refs = {pair: (get_base_ref(*pair), get_remote_dev_ref(pair[1], pair[0])) for pair in heads}
    repos = sorted({repo for repo, _branch in heads})
    wanted = [
        {ref for pair, both in refs.items() if pair[0] == repo for ref in both} for repo in repos
    ]
    shas = dict(zip(repos, executor.map(read_heads, repos, wanted)))
    keys = {
        pair: [head, *(shas[pair[0]].get(ref) for ref in refs[pair])]
        for pair, head in heads.items()
    }
    try:
        kept = json.loads(GIT_CACHE.read_text())
    except (OSError, ValueError):
        kept = {}

    def read(pair):
        found = kept.get(" ".join(pair)) if isinstance(kept, dict) else None
        if isinstance(found, dict) and found.get("key") == keys[pair]:
            return found
        return {
            "commits": get_commits(*pair),
            "key": keys[pair],
            "push": get_push(*pair),
            "status": get_status(*pair, write_tree),
        }

    facts = dict(zip(heads, executor.map(read, heads)))
    fresh = GIT_CACHE.with_name(f".{GIT_CACHE.name}.{os.getpid()}")
    with contextlib.suppress(OSError):
        fresh.write_text(json.dumps({" ".join(pair): found for pair, found in facts.items()}))
        fresh.replace(GIT_CACHE)
    return facts


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


def is_mine(pr):
    """Whether I opened the PR, or fw-bot did as a forward-port of one of mine."""
    author = (pr["author"] or {}).get("login")
    return author == pr["viewer"] or (
        author == FW_BOT and f"{BUNDLE_SUFFIX}-" in pr["headRefName"]
    )


@functools.cache
def get_team():
    """The logins of my team, as the mail agent reads them: those whose trigram is in team.json."""
    try:
        logins = json.loads((MAIL_AGENT / "logins.json").read_text())["logins"]
        trigrams = json.loads((MAIL_AGENT / "team.json").read_text())
    except (OSError, ValueError, KeyError):
        return set()
    return {login for login, trigram in logins.items() if trigram in trigrams}


def get_commanders(pr):
    """Whose robodoo commands count: me, my team, the reviewers asked on the PR, and on a fw-bot
    hop the logins fw-bot pings at the start of its comments. Robodoo refuses the command of
    anyone else, but its text stays."""
    asked = {
        (node.get("requestedReviewer") or {}).get("login") for node in pr["timelineItems"]["nodes"]
    }
    pings = {
        login
        for node in pr["comments"]["nodes"]
        if (node["author"] or {}).get("login") == FW_BOT
        and (match := LEADING_PINGS.match(node["body"] or ""))
        for login in MENTION.findall(match[0])
    }
    return ({pr["viewer"]} | get_team() | asked | pings) - {None}


def get_command_notes(pr):
    """The comments and reviews whose robodoo commands count, as (body, date): from
    get_commanders(), or from a member of the odoo org, such as a reviewer a team request brought,
    but its bots, whose texts quote commands."""
    commanders = get_commanders(pr)
    return [
        (node["body"] or "", node[date] or "")
        for key, date in (("comments", "createdAt"), ("reviews", "submittedAt"))
        for node in pr[key]["nodes"]
        if (login := (node["author"] or {}).get("login")) in commanders
        or (node["authorAssociation"] in ORG_MEMBERS and login not in BOTS)
    ]


def get_overridden(pr, notes):
    """The checks a `@robodoo override=` posted since the head commit sets aside."""
    head_date = pr["commits"]["nodes"][0]["commit"]["committedDate"]
    return {
        context
        for body, date in notes
        if date > head_date
        for match in OVERRIDE.finditer(body)
        for context in match[1].split(",")
    }


def get_pr_facts(pr, repo, now):
    state = "draft" if pr["isDraft"] and pr["state"] == "OPEN" else pr["state"].lower()
    if pr["mergebot"] == "merged":
        state = "merged"
    notes = get_command_notes(pr)
    overridden = get_overridden(pr, notes)
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
        "answers": sorted(t["comments"]["nodes"][0]["createdAt"] for t in to_answer),
        "ask": dict(zip(("style", "label", "date"), get_ask(pr, now))),
        "delegated": any(DELEGATE.search(body) for body, _date in notes),
        "failing": [
            {"context": c["context"], "url": c["targetUrl"]}
            for c in contexts
            if c["state"] in ("ERROR", "FAILURE") and c["context"] not in stale | overridden
        ],
        "head": pr["headRefOid"],
        "mergebot": pr["mergebot"],
        "mine": is_mine(pr),
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
    return get_waiting_group(facts), None


def get_news(repos):
    """What waits on me in a bundle, one item each, to tell what I saw from something new."""
    news = []
    for repo, row in repos.items():
        if not (facts := row["pr"]):
            continue
        head = facts["head"]
        news += [f"{repo} thread {date}" for date in facts["answers"]]
        news += [f"{repo} failing {check['context']} {head}" for check in facts["failing"]]
        if row["conflict"]:
            news.append(f"{repo} conflict {head}")
        if facts["delegated"]:
            news.append(f"{repo} delegated")
        if facts["review"] == "CHANGES_REQUESTED":
            news.append(f"{repo} changes requested")
        if facts["mergebot"] == "error":
            news.append(f"{repo} staging error {head}")
    return sorted(news)


def get_waiting_group(facts):
    """The group of a repo once what waits on me is set aside: a handled bundle goes there."""
    if not facts:
        return "drafts"
    if facts["pending"] or facts["mergebot"] == "approved":
        return "ci"
    if facts["state"] == "draft":
        return "drafts"
    return "reviewer"


def get_bundle_agent(bundle, state, is_open, now):
    alive = is_open or now - state["launches"].get(bundle, 0) < LAUNCH_GRACE
    agent = get_agent(bundle, alive=alive)
    if agent and agent["state"] in ("running", "waiting"):
        return agent
    queue = [item["bundle"] for item in state["queue"]]
    if bundle in queue:
        return {"queued": queue.index(bundle) + 1, "state": "queued"}
    if bundle in state.get("building", []):
        return {"state": "building"}
    return agent


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
        known = read_git_facts(executor, heads, write_tree)
        subjects = dict(zip(repos, executor.map(get_subjects, repos)))
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
            status = known[repo, branch]["status"]
            push = known[repo, branch]["push"]
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
                "commits": known[repo, branch]["commits"],
                "conflict": status and status["conflict"],
                "create_pr": get_compare_url(repo, branch) if create_pr else None,
                "empty": empty,
                "group": group,
                "head": heads[repo, branch],
                "issue": issue,
                "pr": facts,
                "push": push,
                "subject": subjects[repo].get(branch),
            }
        rows = [row for row in repos_of_bundle.values() if not row["empty"]]
        rows = rows or list(repos_of_bundle.values())
        group = min((row["group"] for row in rows), key=GROUP_PRECEDENCE.index)
        issues = {row["issue"] for row in rows if row["group"] == group}
        issue = max(issues - {None}, key=list(ISSUE_RANKS).index, default=None)
        is_open = get_worktree_bundle_folder(branch) in open_folders
        agent = get_bundle_agent(branch, agent_state, is_open, now)
        handled = is_handled(branch, get_news(repos_of_bundle), is_open)
        if group == "me" and agent and agent["state"] in AGENT_WORKING:
            group, issue = "agents", None
        elif group == "me" and handled:
            groups_of_repos = [
                get_waiting_group(row["pr"]) if row["group"] == "me" else row["group"]
                for row in rows
            ]
            group, issue = min(groups_of_repos, key=GROUP_PRECEDENCE.index), None
        bundle = {
            "agent": agent,
            "bundle": branch,
            "date": max(dates.values()),
            "delegated": delegated,
            "group": group,
            "handled": handled,
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


def is_merged_copy(repo, row):
    """Whether the branch holds nothing its merged PR lacks: the PR head is or follows it."""
    pr = row["pr"]
    return (
        pr["state"] == "merged"
        and git(repo, "merge-base", "--is-ancestor", row["head"], pr["head"]) is not None
    )


def get_merged_bundles(bundles):
    """The bundles left with merged PRs and checkouts of their base only, no window, no agent."""
    return [
        bundle
        for bundle in bundles
        if not bundle["open"]
        and not (bundle["agent"] and bundle["agent"]["state"] in AGENT_WORKING)
        and not all(row["empty"] for row in bundle["repos"].values())
        and all(
            row["empty"] or (row["pr"] and is_merged_copy(repo, row))
            for repo, row in bundle["repos"].items()
        )
    ]


def delete_merged_branch(repo, branch, pr):
    """Delete the branch of my merged PR on the dev remote while it is at the PR head: a failure."""
    remote = get_remote_dev_repo(repo)
    res = subprocess.run(
        [
            *GIT,
            "push",
            f"--force-with-lease=refs/heads/{branch}:{pr['head']}",
            remote,
            f":refs/heads/{branch}",
        ],
        capture_output=True,
        check=False,
        cwd=get_repo_folder(repo),
        text=True,
    )
    if res.returncode and "(stale info)" not in res.stderr:
        return f"Merged branch left on {remote}, {repo}/{branch}: {res.stderr.strip()}"
    return None


def delete_merged(bundles):
    """Delete the merged bundles with no uncommitted file: their PRs by name, and the failures."""
    merged = get_merged_bundles(bundles)
    if not merged:
        return {}, []
    runner = UtilsRunner(live=Live("", auto_refresh=False, console=Console(quiet=True)))
    failures = []
    with redirect_stdout(io.StringIO()):
        try:
            names = [
                bundle["bundle"]
                for bundle in merged
                if not any(
                    "uncommitted files" in found
                    for found in get_unsaved_work(runner, bundle["bundle"]).values()
                )
            ]
            delete_bundles(runner, bundle_names=names, force=True)
        except subprocess.CalledProcessError as error:
            failures.append(
                f"Merged bundles not all deleted, {' '.join(error.cmd)}: "
                f"{(error.stderr or '').strip()}",
            )
    deleted = [
        bundle
        for bundle in merged
        if all(
            git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{bundle['bundle']}") is None
            for repo in bundle["repos"]
        )
    ]
    pushes = [
        (repo, bundle["bundle"], row["pr"])
        for bundle in deleted
        for repo, row in bundle["repos"].items()
        if row["pr"] and row["pr"]["mine"]
    ]
    with ThreadPoolExecutor(max_workers=max(len(pushes), 1)) as executor:
        failures += filter(None, executor.map(lambda push: delete_merged_branch(*push), pushes))
    return {
        bundle["bundle"]: [
            f"{repo}#{row['pr']['number']}" for repo, row in bundle["repos"].items() if row["pr"]
        ]
        for bundle in deleted
    }, failures


def get_rows(bundle, group, now):
    branch = bundle["bundle"]
    age = format_age(now - bundle["date"])
    label = f"[cyan]{branch}[/cyan]" if bundle["worktree"] else branch
    icon = "\N{OPEN FILE FOLDER}" if bundle["worktree"] else "\N{INBOX TRAY}"
    opener = f"[link=odoo-bundle://terminal/{branch}]{icon}[/link]"
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
        deleted, failures = delete_merged(bundles)
        print(
            json.dumps(
                {
                    "bundles": [bundle for bundle in bundles if bundle["bundle"] not in deleted],
                    "deleted": deleted,
                    "errors": errors + failures,
                },
            ),
        )
        return

    console = Console()
    with console.status("Reading branches and PRs"):
        bundles, errors = get_bundles()
        deleted, failures = delete_merged(bundles)
    now = time.time()
    groups = {group: [] for group in GROUPS}
    counts = dict.fromkeys(GROUPS, 0)
    for bundle in bundles:
        if bundle["bundle"] in deleted:
            continue
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
    for bundle, prs in deleted.items():
        console.print(f"[magenta]Deleted[/magenta] {bundle}, merged: {', '.join(prs)}")
    for failure in failures:
        console.print(f"[red]{failure}[/red]")
    for error in errors:
        console.print(f"[yellow]PR state incomplete[/yellow], {error}")


if __name__ == "__main__":
    main()
