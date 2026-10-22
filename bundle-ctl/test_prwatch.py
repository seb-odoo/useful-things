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
    prwatch.AGENT = folder / ".agent"
    prwatch.EVENTS = prwatch.AGENT / "pr-events"
    prwatch.STATE = prwatch.AGENT / "prwatch.json"
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
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            watch.run()
        except Stop:
            pass
    check("it waits on the gateway from where the last answer said",
          [url.removeprefix(prwatch.TOUCHED) for url in asked],
          ["", "?after=4", "?after=6", "?after=6"])
    check("a gateway down waits before asking again", slept, [prwatch.RETRY[0]])
    check("one line per PR of the bundle, with what to do of it", prwatch.take(),
          [f"PR event: odoo#1: told {prwatch.ADVICE}"])


def hooked(event):
    out = io.StringIO()
    sys.stdin = io.StringIO(json.dumps({"hook_event_name": event, "session_id": "s1"}))
    with contextlib.redirect_stdout(out):
        prwatch.hook()
    return json.loads(out.getvalue()) if out.getvalue() else None


def test_hooks(folder):
    prwatch.leave("a")
    prwatch.leave("b")
    check("each line waits whole, none half written",
          sorted(path.name.startswith(".") for path in prwatch.EVENTS.iterdir()), [False, False])
    check("a hook gives the agent every line that waits, oldest first", hooked("PostToolUse"),
          {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
                                  f"PR event: a {prwatch.ADVICE}\nPR event: b {prwatch.ADVICE}"}})
    check("once", hooked("UserPromptSubmit"), None)
    check("and leaves nothing behind", list(prwatch.EVENTS.iterdir()), [])

    sys.modules["prwatch"] = prwatch
    spec = importlib.util.spec_from_file_location("stop", HERE / "client" / "agent-stop.py")
    stop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stop)
    stop.AGENT = prwatch.AGENT
    (prwatch.AGENT / "session").write_text("s1\n")
    (prwatch.AGENT / "terminal").write_text("1\n")
    prwatch.leave("c")

    def stopped():
        out = io.StringIO()
        sys.stdin = io.StringIO(json.dumps({"session_id": "s1", "last_assistant_message": "Pushed: x"}))
        with contextlib.redirect_stdout(out):
            stop.main()
        return out.getvalue(), (prwatch.AGENT / "done").exists()

    check("a line that waits keeps the turn going, with no verdict written", stopped(),
          (json.dumps({"decision": "block", "reason": f"PR event: c {prwatch.ADVICE}"}) + "\n",
           False))
    check("a turn before the terminal typed its task writes no verdict", stopped(), ("", False))
    (prwatch.AGENT / "run.lock").mkdir()
    check("then the turn ends on its verdict", stopped(), ("", True))

    started = []
    prwatch.Watch.run = lambda self: started.append(self)
    sys.argv = ["prwatch.py"]
    with (prwatch.AGENT / "prwatch.lock").open("w") as held:
        prwatch.fcntl.flock(held, prwatch.fcntl.LOCK_EX)
        prwatch.main()
    check("a second watch in the same container ends at once", started, [])
    prwatch.main()
    check("the first one watches", len(started), 1)


def test_posted(folder):
    """What Seb posted from Firefox, left by pr-posted.py on the host for the agent of a bundle."""
    spec = importlib.util.spec_from_file_location("posted", HERE / "pr-posted.py")
    posted = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(posted)
    posted.get_agent_folder = lambda bundle: folder / (".agent" if bundle == BUNDLE else "none")

    def run(bundle, line):
        sys.argv = ["pr-posted.py", bundle]
        sys.stdin = io.StringIO(line)
        try:
            posted.main()
        except SystemExit as error:
            return str(error)
        return None

    line = "odoo/odoo#7 (u): seb-odoo posted from Firefox a comment (u5)."
    check("left for the agent of the bundle", (run(BUNDLE, f"{line}\n"), prwatch.take()),
          (None, [f"PR event: {line} {posted.ADVICE}"]))
    check("a bundle with no agent", run("other--seb", line), "other--seb has no agent folder")
    check("not a bundle", run("../.agent", line), "../.agent has no agent folder")
    for wrong in ("", "a\nb", "x" * 2001):
        check(f"not one line {wrong[:5]!r}", run(BUNDLE, wrong), "one line of 2000 characters at most on stdin")
    check("nothing left", prwatch.take(), [])


def main():
    with tempfile.TemporaryDirectory() as name:
        make_workspace(pathlib.Path(name))
        test_look(pathlib.Path(name))
        test_run()
        test_hooks(pathlib.Path(name))
        test_posted(pathlib.Path(name))
    print("\n".join(failures) or "all good")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
