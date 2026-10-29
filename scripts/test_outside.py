"""A bundle on a PR of an author outside the odoo org: its mark, its copy of the memory, and what
of it comes to Seb as a proposal: python3 test_outside.py"""

import json
import os
import pathlib
import sys
import tempfile
import time

import outside

HERE = pathlib.Path(__file__).resolve().parent
BUNDLE = "master-fix-thing"
FAKE_GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_GH_LOG"
[ -s "$FAKE_GH_ANSWER" ] || { echo "no network" >&2; exit 1; }
cat "$FAKE_GH_ANSWER"
"""


def pr(association, head, url="https://github.com/odoo/odoo/pull/1"):
    return {
        "url": url,
        "authorAssociation": association,
        "author": {"login": "someone"},
        "headRepositoryOwner": {"login": head},
    }


def main():
    failures, total = [], 0

    def check(name, found, expected):
        nonlocal total
        total += 1
        if found != expected:
            failures.append(f"{name}: {found!r}, expected {expected!r}")

    text = (HERE.parent / "devcontainer" / "base.jsonc").read_text()
    source = "claude-config/${localWorkspaceFolderBasename}/memory"
    target = "target=/home/vscode/.claude/projects/-workspace/memory,"
    mounted = [line for line in text.splitlines() if target in line]
    check("base.jsonc mounts the memory of each bundle from its folder", len(mounted), 1)
    check("from claude-config/<bundle>/memory", source in "".join(mounted), True)

    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        outside.OWN = root / "claude-config"
        outside.SHARED = root / "projects" / "-workspace" / "memory"
        outside.INBOX = root / "handoff"
        outside.SHARED.mkdir(parents=True)
        (root / "bin").mkdir()
        (root / "bin" / "gh").write_text(FAKE_GH)
        (root / "bin" / "gh").chmod(0o755)
        os.environ["PATH"] = f"{root / 'bin'}:{os.environ['PATH']}"
        answer = root / "answer.json"
        os.environ["FAKE_GH_ANSWER"] = str(answer)
        os.environ["FAKE_GH_LOG"] = str(root / "gh.log")

        def answer_prs(*prs):
            nodes = {"nodes": list(prs)}
            answer.write_text(json.dumps({"data": {"r0": {"pullRequests": nodes}}}))

        def reset(bundle):
            (outside.OWN / bundle / "outside").unlink(missing_ok=True)

        rest = {
            "html_url": "https://github.com/odoo/odoo/pull/7",
            "user": {"login": "stranger"},
            "author_association": "CONTRIBUTOR",
            "head": {"repo": {"owner": {"login": "stranger"}}},
        }
        answer.write_text(json.dumps(rest))
        found = outside.check_fetch(BUNDLE, "https://github.com/odoo/odoo/pull/7")
        check("a fetch of a PR of a contributor marks it", (found or {}).get("author"), "stranger")
        reset(BUNDLE)
        answer.write_text(json.dumps({**rest, "author_association": "MEMBER"}))
        check("a PR of a member does not", outside.check_fetch(BUNDLE, rest["html_url"]), None)
        fw = {
            **rest,
            "author_association": "NONE",
            "head": {"repo": {"owner": {"login": "odoo-dev"}}},
        }
        answer.write_text(json.dumps(fw))
        check("nor a forward-port on odoo-dev", outside.check_fetch(BUNDLE, rest["html_url"]), None)
        answer.write_text("")
        found = outside.check_fetch(BUNDLE, rest["html_url"])
        check("GitHub out of reach at a fetch counts as outside", bool(found), True)
        reset(BUNDLE)
        check("a dev branch is no PR", outside.check_fetch(BUNDLE, f"odoo-dev:{BUNDLE}"), None)
        answer_prs(pr("NONE", "other"), pr("MEMBER", "colleague"))
        found = outside.check_fetch(BUNDLE, f"colleague:{BUNDLE}")
        check("a fork label takes the PR of that fork", found, None)
        answer_prs()
        found = outside.check_fetch(BUNDLE, f"stranger:{BUNDLE}")
        check("a fork label with no PR counts as outside", (found or {}).get("author"), "stranger")
        reset(BUNDLE)

        folder = root / "bundle"
        (folder / "odoo").mkdir(parents=True)
        answer_prs(pr("MEMBER", "odoo-dev"))
        check("a start with PRs of members only", outside.check_start(BUNDLE, folder), None)
        answer.write_text("")
        check("a start with GitHub out of reach", outside.check_start(BUNDLE, folder), None)
        outside.prepare(BUNDLE)
        copy = outside.OWN / BUNDLE / "memory"
        check("an unmarked bundle mounts the shared memory", os.readlink(copy), str(outside.SHARED))

        index = outside.SHARED / "MEMORY.md"
        index.write_text("- [one](one.md): first\n")
        (outside.SHARED / "one.md").write_text("one\n")
        answer_prs(pr("FIRST_TIME_CONTRIBUTOR", "stranger"))
        check(
            "an open PR of a stranger marks it at its start",
            bool(outside.check_start(BUNDLE, folder)),
            True,
        )
        outside.prepare(BUNDLE)
        check("a marked bundle gets a copy", copy.is_dir() and not copy.is_symlink(), True)
        check("which holds the shared notes", (copy / "MEMORY.md").read_text(), index.read_text())

        (copy / "MEMORY.md").write_text("- [one](one.md): first\n- [two](two.md): mine\n")
        (copy / "two.md").write_text("two\n")
        index.write_text("- [one](one.md): first\n- [three](three.md): another bundle\n")
        secret = root / "secret"
        secret.write_text("a secret of the host\n")
        (copy / "leak.md").symlink_to(secret)
        check("notes written a moment ago wait", outside.collect(BUNDLE, outside.QUIET), [])
        past = time.time() - 120
        for path in (copy / "MEMORY.md", copy / "two.md"):
            os.utime(path, (past, past))
        found = outside.collect(BUNDLE, outside.QUIET)
        check("a minute later they are proposed", found, ["MEMORY.md", "two.md"])
        inbox = outside.INBOX / f"{BUNDLE}@memory"
        proposed = inbox / "dev-memory" / "MEMORY.md"
        merged = (
            "- [one](one.md): first\n- [three](three.md): another bundle\n- [two](two.md): mine\n"
        )
        check("merged into what the shared memory got since", proposed.read_text(), merged)
        check("the link is not read", (inbox / "dev-memory" / "leak.md").exists(), False)
        why = (inbox / "WHY").read_text()
        check("its reason names the PR", "https://github.com/odoo/odoo/pull/1" in why, True)
        (copy / "four.md").write_text("four\n")
        os.utime(copy / "four.md", (past, past))
        check("a newer note waits for the answer", outside.collect(BUNDLE, outside.QUIET), [])
        index.write_text("- [one](one.md): first\n- [five](five.md): yet another\n")
        outside.prepare(BUNDLE)
        found = [(copy / name).exists() for name in ("two.md", "four.md")]
        check("a restart keeps what waits and what is not proposed yet", found, [True, True])
        check("and the index that waits", "mine" in (copy / "MEMORY.md").read_text(), True)

        def answer(written):
            for name in written:
                (outside.SHARED / name).write_bytes((inbox / "dev-memory" / name).read_bytes())
            for path in (inbox / "dev-memory").iterdir():
                path.unlink()
            (inbox / "WHY").unlink()

        answer(["MEMORY.md", "two.md"])
        check("after a yes, the newer note comes", outside.collect(BUNDLE), ["four.md"])
        answer([])
        outside.prepare(BUNDLE)
        check("a no drops a note from the copy", (copy / "four.md").exists(), False)
        check("a yes leaves it there", (copy / "two.md").read_text(), "two\n")
        check(
            "the copy takes the shared index", (copy / "MEMORY.md").read_text(), index.read_text()
        )
        check("the link stays where it is", (copy / "leak.md").is_symlink(), True)
        check("its target untouched", secret.read_text(), "a secret of the host\n")

        sys.path.insert(0, str(HERE.parent / "bundle-ctl"))
        import remote_agent

        own = outside.OWN / BUNDLE
        check("a remote agent takes the copy", remote_agent.get_memory(own), own / "memory")
        shared = remote_agent.HOME / ".claude" / "projects" / "-workspace" / "memory"
        check("and the shared one elsewhere", remote_agent.get_memory(outside.OWN / "x"), shared)
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
