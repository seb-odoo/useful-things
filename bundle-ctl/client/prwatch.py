#!/usr/bin/env python3
"""Tell the agent of a bundle when someone else acts on one of its PRs.

client/agent-run.sh --terminal starts it beside claude, once per container. It waits on the mail
gateway, where mail-watch names the PRs of each new GitHub mail (`GET /_gateway/touched`), and for
a PR of the bundle reads on GitHub what came since its last look: reviews and comments of others,
a head the bundle does not have, a close. It leaves one line per PR in .agent/pr-events/, which
names them and never carries their text: the agent reads that with gh.

One of three hands each line to the agent, once: this script as a PostToolUse or UserPromptSubmit
hook (`--hook`), client/agent-stop.py before the turn ends, or agent-terminal.py, which types it
as the next prompt of an idle session.

    prwatch.py            the watch
    prwatch.py --hook     the lines that wait, as context of a hook
"""

import contextlib
import datetime
import fcntl
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.parse
import urllib.request

WORKSPACE = pathlib.Path("/workspace")
AGENT = WORKSPACE / ".agent"
EVENTS = AGENT / "pr-events"
STATE = AGENT / "prwatch.json"
TOUCHED = "https://gmail.googleapis.com/_gateway/touched"
GITHUB = "https://api.github.com"
OWNER = "odoo"
WAIT = 70
RETRY = (30, 600)
LIST_AGAIN = 600
REVIEWS = {
    "APPROVED": ("approval", "approvals"),
    "CHANGES_REQUESTED": ("request for changes", "requests for changes"),
    "COMMENTED": ("review", "reviews"),
    "DISMISSED": ("dismissed review", "dismissed reviews"),
}
ADVICE = (
    "Read what came with gh before anything else. What is Seb's call (an answer to a reviewer, a"
    " change of approach, work over someone else's push), prepare and ask him; what follows from"
    " your task and his rules, do. Open your reply on what came and what you did with it."
)


def log(text):
    print(f"{time.strftime('%H:%M:%S')} {text}", file=sys.stderr, flush=True)


def get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def github(path):
    return get(f"{GITHUB}/{path}")


def now():
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path):
    try:
        found = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return found if type(found) is dict else {}


def write_state(state):
    try:
        STATE.with_suffix(".tmp").write_text(json.dumps(state, indent=1))
        STATE.with_suffix(".tmp").replace(STATE)
    except OSError as error:
        log(f"state not kept: {error}")


def leave(text, folder=None, advice=ADVICE):
    """One line for the agent, whole or not at all for whoever takes it."""
    folder = folder or EVENTS
    folder.mkdir(exist_ok=True)
    name = f"{time.time_ns()}.txt"
    (folder / f".{name}").write_text(f"PR event: {text} {advice}")
    (folder / f".{name}").replace(folder / name)


def take(folder=None):
    """The lines that wait, oldest first, each taken by one caller only: the hooks in the container
    and agent-terminal.py on the host take from the same folder. Each one taken is noted in
    prwatch.log, with who took it and how long after it was left."""
    folder = folder or EVENTS
    taken = []
    for path in sorted(folder.glob("[!.]*.txt")):
        mine = path.with_name(f".{path.name}.taken")
        try:
            path.rename(mine)
            taken.append(mine.read_text().strip())
            mine.unlink()
        except OSError:
            continue
        waited = (time.time_ns() - int(path.stem)) / 1e9 if path.stem.isdigit() else 0
        who, stamp = pathlib.Path(sys.argv[0]).name, time.strftime("%H:%M:%S")
        with contextlib.suppress(OSError), (folder.parent / "prwatch.log").open("a") as file:
            file.write(f"{stamp} taken by {who} after {waited:.1f} s: {taken[-1][:100]}\n")
    return taken


def git(repo, *args):
    return subprocess.run(["git", "-C", str(WORKSPACE / repo), *args], capture_output=True,
                          check=False, text=True)


def branches():
    """The repos of the bundle whose worktree is on its branch: the bundle is named after it."""
    bundle = os.environ.get("ODOO_PROXY_HOST", "")
    if not bundle:
        return {}
    found = {}
    for folder in sorted(WORKSPACE.iterdir()):
        if (folder / ".git").exists() and git(folder.name, "branch", "--show-current"
                                              ).stdout.strip() == bundle:
            found[folder.name] = bundle
    return found


def list_prs():
    """`repo#number` of each PR whose head is the branch of the bundle, in any fork."""
    prs = {}
    for repo, branch in branches().items():
        query = urllib.parse.quote(f"is:pr repo:{OWNER}/{repo} head:{branch}")
        for item in github(f"search/issues?q={query}")["items"]:
            prs[f"{repo}#{item['number']}"] = repo
    return prs


def is_here(repo, sha):
    """Whether the bundle has this commit in its branch: it pushed it, or fetched and kept it."""
    return git(repo, "merge-base", "--is-ancestor", sha, "HEAD").returncode == 0


def by_whom(items, me, since, at="created_at"):
    """How many of these items each login but `me` wrote after `since`. Not the ones edited
    since: robodoo edits its status comment at each check of the PR."""
    counts = {}
    for item in items:
        login = (item.get("user") or {}).get("login", "")
        if login and login != me and (item.get(at) or "") > since:
            counts[login] = counts.get(login, 0) + 1
    return counts


def counted(counts, singular, plural):
    return [f"{count} {singular if count == 1 else plural} by {login}"
            for login, count in counts.items()]


def look(pr, repo, seen, me):
    """What came on this PR since `seen` (its last look), as one line, or "" when nothing did."""
    number = pr.split("#")[1]
    base = f"repos/{OWNER}/{repo}"
    since = seen.get("since") or now()
    looked = now()
    pull = github(f"{base}/pulls/{number}")
    said = []
    reviews = github(f"{base}/pulls/{number}/reviews?per_page=100")
    for state, words in REVIEWS.items():
        mine = [one for one in reviews if one.get("state") == state]
        said += counted(by_whom(mine, me, since, "submitted_at"), *words)
    quoted = urllib.parse.quote(since)
    for words, path in ((("review comment", "review comments"), f"pulls/{number}/comments"),
                        (("comment", "comments"), f"issues/{number}/comments")):
        got = github(f"{base}/{path}?since={quoted}&per_page=100")
        said += counted(by_whom(got, me, since), *words)
    head = pull["head"]["sha"]
    if head != seen.get("head") and not is_here(repo, head):
        said.append(f"its head is now {head[:12]}, which this bundle does not have")
    if pull["state"] == "closed" and seen.get("state") != "closed":
        said.append("it is closed (mergebot says whether it was merged)")
    seen.update(since=looked, head=head, state=pull["state"])
    if not said:
        return ""
    url = f"https://github.com/{OWNER}/{repo}/pull/{number}"
    return f"{pr} ({url}), since {since}: " + "; ".join(said) + "."


class Watch:
    def __init__(self):
        self.state = read_json(STATE)
        self.prs = {}
        self.listed = float("-inf")
        self.me = ""

    def list_again(self):
        """The PRs of the bundle, each looked at from now on, from the head GitHub has now: the
        bundle may have changed its commit since its last push."""
        self.prs = list_prs()
        self.listed = time.monotonic()
        for pr, repo in self.prs.items():
            if pr not in self.state:
                pull = github(f"repos/{OWNER}/{repo}/pulls/{pr.split('#')[1]}")
                self.state[pr] = {"since": now(), "head": pull["head"]["sha"],
                                  "state": pull["state"]}
        write_state(self.state)

    def told(self, pr):
        """What to tell the agent of a PR mail named, "" for one that is not of the bundle. A PR
        of a repo where the bundle has none yet lists them again, 10 minutes apart at most: Seb
        may have opened it since."""
        repo = pr.split("#")[0]
        if pr not in self.prs:
            if (repo in self.prs.values() or repo not in branches()
                    or time.monotonic() - self.listed < LIST_AGAIN):
                return ""
            self.list_again()
            if pr not in self.prs:
                return ""
        self.me = self.me or github("user")["login"]
        text = look(pr, self.prs[pr], self.state.setdefault(pr, {}), self.me)
        write_state(self.state)
        return text

    def run(self):
        after, pause = None, RETRY[0]
        while True:
            try:
                if self.listed == float("-inf"):
                    self.list_again()
                url = TOUCHED if after is None else f"{TOUCHED}?after={after}"
                answer = get(url, timeout=WAIT)
                for pr in dict.fromkeys(one["pr"] for one in answer["touched"]):
                    text = self.told(pr)
                    if text:
                        leave(text)
                        log(f"told: {text}")
                after, pause = answer["next"], RETRY[0]
            except (OSError, ValueError, KeyError, TypeError) as error:
                log(f"{error!r}, again in {pause} s")
                time.sleep(pause)
                pause = min(pause * 2, RETRY[1])


def hook():
    """As a PostToolUse or UserPromptSubmit hook: the lines that wait, as context of the agent."""
    event = json.load(sys.stdin).get("hook_event_name", "")
    told = take()
    if told:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                                 "additionalContext": "\n".join(told)}}))


def main():
    if sys.argv[1:] == ["--hook"]:
        hook()
        return
    lock = (AGENT / "prwatch.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    Watch().run()


if __name__ == "__main__":
    main()
