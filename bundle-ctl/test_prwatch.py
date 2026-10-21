#!/usr/bin/env python3
"""What client/prwatch.py tells the agent of a bundle, with the mail gateway and GitHub faked and a
real git repo as its worktree: python3 test_prwatch.py"""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.error

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("prwatch", HERE / "client" / "prwatch.py")
prwatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prwatch)

BUNDLE = "19.0-fix-x--seb"
SINCE = "2026-10-21T10:00:00Z"
BEFORE = "2026-10-21T09:00:00Z"
AFTER = "2026-10-21T11:00:00Z"
failures = []


class Stop(BaseException):
    pass


def check(what, got, expected):
    if got != expected:
        failures.append(f"{what}: got {got!r}, expected {expected!r}")


def git(*args):
    return subprocess.run(["git", "-C", str(prwatch.WORKSPACE / "odoo"), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def make_workspace(folder):
    prwatch.WORKSPACE = folder
    prwatch.STATE = folder / ".agent" / "prwatch.json"
    (folder / ".agent").mkdir()
    (folder / "odoo").mkdir()
    (folder / "upgrade-util").mkdir()
    git("init", "-q", "-b", BUNDLE)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "one")
    subprocess.run(["git", "-C", str(folder / "upgrade-util"), "init", "-q", "-b", "master"],
                   check=True)
    os.environ["ODOO_PROXY_HOST"] = BUNDLE


class GitHub:
    """Answers by path; each answer is replaced by the test as the PR moves on."""

    def __init__(self, head):
        self.asked = []
        self.pages = {
            "user": {"login": "seb-odoo"},
            "repos/odoo/odoo/pulls/1": {"head": {"sha": head}, "state": "open"},
            "repos/odoo/odoo/pulls/1/reviews": [],
            "repos/odoo/odoo/pulls/1/comments": [],
            "repos/odoo/odoo/issues/1/comments": [],
        }

    def get(self, url, timeout=30):
        path = url.removeprefix(f"{prwatch.GITHUB}/")
        self.asked.append(path)
        if path.startswith("search/issues?q="):
            query = prwatch.urllib.parse.unquote(path.split("=", 1)[1])
            found = query == f"is:pr repo:odoo/odoo head:{BUNDLE}"
            return {"items": [{"number": 1}] if found else []}
        return self.pages[path.split("?")[0]]


def item(login, created, **more):
    return {"user": {"login": login}, "created_at": created, **more}


def test_look(folder):
    head = git("rev-parse", "HEAD")
    hub = GitHub(head)
    prwatch.get = hub.get
    watch = prwatch.Watch()
    watch.list_again()
    check("the PR of the bundle's branch, not the one of master in another repo", watch.prs,
          {"odoo#1": "odoo"})
    check("the searches", [one for one in hub.asked if one.startswith("search")], [
        f"search/issues?q=is%3Apr%20repo%3Aodoo/odoo%20head%3A{BUNDLE}"])
    check("a PR is looked at from the start of the session, from the head GitHub has",
          (set(watch.state["odoo#1"]), watch.state["odoo#1"]["head"]),
          ({"since", "head", "state"}, head))
    watch.state["odoo#1"]["since"] = SINCE
    hub.pages["repos/odoo/odoo/pulls/1/reviews"] = [
        {"user": {"login": "aku-odoo"}, "state": "CHANGES_REQUESTED", "submitted_at": AFTER},
        {"user": {"login": "aku-odoo"}, "state": "APPROVED", "submitted_at": BEFORE},
        {"user": {"login": "seb-odoo"}, "state": "PENDING"},
        {"user": {"login": "seb-odoo"}, "state": "COMMENTED", "submitted_at": AFTER},
    ]
    hub.pages["repos/odoo/odoo/pulls/1/comments"] = [
        item("aku-odoo", AFTER), item("aku-odoo", AFTER), item("seb-odoo", AFTER)]
    hub.pages["repos/odoo/odoo/issues/1/comments"] = [
        item("robodoo", BEFORE, updated_at=AFTER), item("fw-bot", AFTER)]
    told = watch.told("odoo#1")
    check("what came, by whom, with no text of it", told,
          f"odoo#1 (https://github.com/odoo/odoo/pull/1), since {SINCE}: 1 request for changes"
          " by aku-odoo; 2 review comments by aku-odoo; 1 comment by fw-bot.")
    check("the comments read since the last look",
          [one for one in hub.asked if "comments?" in one][-2:],
          [f"repos/odoo/odoo/{path}/comments?since=2026-10-21T10%3A00%3A00Z&per_page=100"
           for path in ("pulls/1", "issues/1")])
    check("the look is kept", json.loads(prwatch.STATE.read_text())["odoo#1"]["head"], head)
    since = watch.state["odoo#1"]["since"]
    for page in ("pulls/1/reviews", "pulls/1/comments", "issues/1/comments"):
        hub.pages[f"repos/odoo/odoo/{page}"] = []
    check("nothing new, nothing told", watch.told("odoo#1"), "")
    check("and the next look starts later", watch.state["odoo#1"]["since"] >= since, True)

    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "two")
    hub.pages["repos/odoo/odoo/pulls/1"] = {"head": {"sha": git("rev-parse", "HEAD")},
                                            "state": "open"}
    check("a push of the bundle's own commit is not told", watch.told("odoo#1"), "")
    hub.pages["repos/odoo/odoo/pulls/1"] = {"head": {"sha": "f" * 40}, "state": "closed"}
    check("a head the bundle does not have, and a close", watch.told("odoo#1").split(": ", 1)[1],
          "its head is now ffffffffffff, which this bundle does not have; it is closed (mergebot"
          " says whether it was merged).")
    check("each told once", watch.told("odoo#1"), "")

    hub.asked.clear()
    check("a PR of a repo the bundle does not have", watch.told("enterprise#7"), "")
    check("a PR of odoo, where the bundle knows its own", watch.told("odoo#8"), "")
    check("cost no GitHub read", hub.asked, [])
    watch.prs = {}
    watch.listed = float("-inf")
    check("a PR of odoo, where the bundle knows none, lists them again",
          (watch.told("odoo#8"), [one[:6] for one in hub.asked]), ("", ["search"]))
    hub.asked.clear()
    check("10 minutes apart at most", (watch.told("odoo#9"), hub.asked), ("", []))


def test_run():
    answers = [
        {"next": 4, "touched": []},
        {"next": 6, "touched": [{"pr": "odoo#2", "at": 1}, {"pr": "odoo#1", "at": 1},
                                {"pr": "odoo#2", "at": 1}]},
        urllib.error.URLError("gateway down"),
        Stop(),
    ]
    asked, slept = [], []

    def gateway(url, timeout=30):
        asked.append(url)
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    watch = prwatch.Watch()
    watch.listed = 0
    watch.told = lambda pr: f"{pr}: told" if pr == "odoo#1" else ""
    prwatch.get = gateway
    prwatch.time.sleep = slept.append
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            watch.run()
        except Stop:
            pass
    check("it waits on the gateway from where the last answer said",
          [url.removeprefix(prwatch.TOUCHED) for url in asked], ["", "?after=4", "?after=6", "?after=6"])
    check("a gateway down waits before asking again", slept, [prwatch.RETRY[0]])
    check("one channel event per PR of the bundle",
          [json.loads(line) for line in out.getvalue().splitlines()],
          [{"jsonrpc": "2.0", "method": "notifications/claude/channel",
            "params": {"content": "odoo#1: told", "meta": {"pr": "odoo#1"}}}])


class Thread:
    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        self.target()


def test_protocol():
    started = []
    prwatch.Watch.run = lambda self: started.append(self)
    prwatch.threading.Thread = Thread
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sys.stdin = io.StringIO("\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2025-11-25"}}),
            "not json",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n")
        prwatch.main()
    sent = [json.loads(line) for line in out.getvalue().splitlines()]
    check("a channel, in the protocol claude asked",
          {key: sent[0]["result"][key] for key in ("protocolVersion", "capabilities")},
          {"protocolVersion": "2025-11-25", "capabilities": {"experimental": {"claude/channel": {}}}})
    check("with what to do of an event", "Seb's call" in sent[0]["result"]["instructions"], True)
    check("a ping is answered, any other request refused",
          [(one["id"], "result" in one) for one in sent[1:]], [(1, True), (2, False)])
    check("the watch starts once", len(started), 1)


def test_allowed(folder):
    prwatch.CONFIG = folder
    for what, account, settings, expected in (
        ("a Max account", {"organizationType": "claude_max"}, None, True),
        ("a seat of a Team", {"organizationType": "claude_team"}, {}, False),
        ("of an Enterprise", {"organizationType": "claude_enterprise"}, None, False),
        ("of a Team that turned channels on", {"organizationType": "claude_team"},
         {"channelsEnabled": True}, True),
    ):
        (folder / ".claude.json").write_text(json.dumps({"oauthAccount": account}))
        (folder / "remote-settings.json").unlink(missing_ok=True)
        if settings is not None:
            (folder / "remote-settings.json").write_text(json.dumps(settings))
        check(f"a channel for {what}", prwatch.is_allowed(), expected)


def main():
    with tempfile.TemporaryDirectory() as name:
        make_workspace(pathlib.Path(name))
        test_look(pathlib.Path(name))
        test_run()
    with tempfile.TemporaryDirectory() as name:
        test_allowed(pathlib.Path(name))
    test_protocol()
    print("\n".join(failures) or "all good")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
