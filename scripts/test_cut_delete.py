#!/usr/bin/env python3
"""A delete run again on a bundle whose removal a kill cut, on scratch repos, with podman and
postgres faked: python3 test_cut_delete.py"""

import pathlib
import sys
import tempfile

from test_optional_repos import git, prepare, run

BUNDLE = "master-cut--me"
TRACKED = pathlib.Path("addons", "web", "tooling", "enable.sh")
CUT = """
import os, pathlib, sys, command_runner, delete_bundle, utils
run = command_runner.Runner.run

def cut(self, cmd, **kwargs):
    if "remove" in cmd and cmd[cmd.index("remove") + 1].endswith("/odoo"):
        pathlib.Path(cmd[cmd.index("remove") + 1], sys.argv[2]).unlink()
        os._exit(137)
    return run(self, cmd, **kwargs)

command_runner.Runner.run = cut
delete_bundle.delete_bundle(utils.UtilsRunner(), bundle_name=sys.argv[1])
"""


def check(root):
    env = prepare(root)
    fakes = root / "fakes"
    fakes.mkdir()
    for name in ("podman", "psql", "dropdb"):
        (fakes / name).write_text("#!/bin/sh\nexit 0\n")
        (fakes / name).chmod(0o755)
    env.update(
        CACHE_ROOT=str(root / "cache"),
        PATH=f"{fakes}:{env['PATH']}",
        STATE_ROOT=str(root / "state"),
        XDG_RUNTIME_DIR=str(root / "run"),
    )
    bundle = root / "src" / "master" / BUNDLE
    odoo = bundle / "odoo"
    deleting = root / "state" / "bundle-ctl" / "deleting" / BUNDLE
    made = bundle / ".made"
    found = {}

    def create():
        code, out = run(env, "create_bundle.py", "master", "cut", "--no-push", "--no-open",
                        "--branch-repo", "odoo")
        if code:
            raise RuntimeError(f"create_bundle.py master cut: {out}")

    def delete():
        code, out = run(env, "delete_bundle.py", BUNDLE)
        return code, bundle.exists(), " D addons/web/tooling/enable.sh" in out

    create()
    (odoo / TRACKED).unlink()
    found["a file Seb deleted is still work"] = (delete(), (1, True, True))
    git(odoo, "checkout", "--", str(TRACKED))
    code, _out = run(env, "-c", CUT, BUNDLE, str(TRACKED))
    found["the run is killed in the removal of odoo, with its mark left and no .made"] = (
        (
            code,
            (odoo / ".git").exists(),
            (odoo / TRACKED).exists(),
            deleting.exists(),
            made.exists(),
        ),
        (137, True, False, True, False),
    )
    (odoo / "notes").write_text("mine\n")
    found["a file added since is still work after a cut"] = (delete(), (1, True, False))
    (odoo / "notes").unlink()
    found["the next run removes the cut bundle and its mark"] = (
        (delete(), deleting.exists(), git(root / "repo" / "odoo", "branch", "--list", BUNDLE)),
        ((0, False, False), False, ""),
    )
    create()
    made.unlink()
    deleting.touch()
    create()
    found["a build of a half-made bundle drops its mark"] = (
        (made.exists(), deleting.exists()),
        (True, False),
    )
    french = {**env, "LANG": "C.UTF-8", "LANGUAGE": "fr"}
    french.pop("LC_ALL", None)
    code, _out = run(french, "delete_bundle.py", BUNDLE)
    found["a delete whose git speaks French removes the bundle"] = (
        (code, bundle.exists()),
        (0, False),
    )
    return found


def main():
    with tempfile.TemporaryDirectory() as root:
        found = check(pathlib.Path(root))
    failures = [
        f"{case}: {got!r}, expected {expected!r}"
        for case, (got, expected) in found.items()
        if got != expected
    ]
    for line in failures:
        print(line)
    print(f"{len(found) - len(failures)}/{len(found)} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
