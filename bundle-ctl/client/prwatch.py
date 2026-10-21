#!/usr/bin/env python3
"""Tell the agent of a bundle when someone else acts on one of its PRs: a Claude Code channel.

claude starts it (`client/agent-run.sh --terminal`) and gets each event as a `<channel
source="prwatch">` message, which starts a turn on an idle session and reaches a busy one at its
next step. It waits on the mail gateway, where mail-watch names the PRs of each new GitHub mail
(`GET /_gateway/touched`), and for a PR of the bundle reads on GitHub what came since its last
look: reviews and comments of others, a head the bundle does not have, a close. The event names
them and never carries their text: the agent reads that with gh.

    prwatch.py --allowed      exit 0 when the account claude is logged in with takes a channel
"""

import datetime
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CONFIG = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
ORGANIZATIONS = ("claude_team", "claude_enterprise")
WORKSPACE = pathlib.Path("/workspace")
STATE = WORKSPACE / ".agent" / "prwatch.json"
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
INSTRUCTIONS = """\
prwatch sends a <channel source="prwatch"> message when someone else acts on a PR of this bundle:
a review, a comment, a push this bundle does not have, a close. It names what came, never its text:
read it with gh (the PR, its reviews and comments; git fetch for a push) before anything else.
Then decide as for any other work of this bundle: what is Seb's call (an answer to a reviewer, a
change of approach, work over someone else's push) you prepare and ask him; what follows from your
task and his rules you do. Either way, your reply opens on what came and what you did with it."""

out = threading.Lock()


def send(message):
    with out:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def say(text, pr):
    send({"jsonrpc": "2.0", "method": "notifications/claude/channel",
          "params": {"content": text, "meta": {"pr": pr}}})


def log(text):
    print(f"prwatch: {text}", file=sys.stderr, flush=True)


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


def read_state():
    return read_json(STATE)


def is_allowed():
    """Whether claude may load a channel for the account it is logged in with: a seat of a Team or
    Enterprise organization only once its admin turned channels on. Otherwise claude drops the
    events and says so under its banner, in every agent terminal."""
    account = read_json(CONFIG / ".claude.json").get("oauthAccount") or {}
    return (account.get("organizationType") not in ORGANIZATIONS
            or read_json(CONFIG / "remote-settings.json").get("channelsEnabled") is True)


def write_state(state):
    try:
        STATE.with_suffix(".tmp").write_text(json.dumps(state, indent=1))
        STATE.with_suffix(".tmp").replace(STATE)
    except OSError as error:
        log(f"state not kept: {error}")


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
        self.state = read_state()
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
                        say(text, pr)
                after, pause = answer["next"], RETRY[0]
            except (OSError, ValueError, KeyError, TypeError) as error:
                log(f"{error!r}, again in {pause} s")
                time.sleep(pause)
                pause = min(pause * 2, RETRY[1])


def reply(message, started):
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {
            "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"experimental": {"claude/channel": {}}},
            "serverInfo": {"name": "prwatch", "version": "1"},
            "instructions": INSTRUCTIONS,
        }})
    elif method == "notifications/initialized" and not started.is_set():
        started.set()
        threading.Thread(target=Watch().run, daemon=True).start()
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
    elif "id" in message and method:
        send({"jsonrpc": "2.0", "id": message["id"],
              "error": {"code": -32601, "message": f"no {method}"}})


def main():
    if sys.argv[1:] == ["--allowed"]:
        sys.exit(0 if is_allowed() else 1)
    started = threading.Event()
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if type(message) is dict:
            reply(message, started)


if __name__ == "__main__":
    main()
