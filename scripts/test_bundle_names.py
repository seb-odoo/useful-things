#!/usr/bin/env python3
"""What fetch_bundle.py and proxy.py do with a branch name that is no bundle name, with GitHub,
runbot and podman faked: python3 test_bundle_names.py"""

import json
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import proxy

FETCH = """
import runpy, sys
import branch_status, commands, requests, utils
done, head, name = [], sys.argv[1], sys.argv[2]
branch_status.get_pr = lambda *args: {
    "baseRefName": "master",
    "headRefName": head,
    "headRepository": {"name": "odoo"},
    "headRepositoryOwner": {"login": "odoo"},
}
branch_status.get_github_repo = lambda repo, remote=None: "odoo/odoo"
commands.get_repos = lambda: ["odoo"]
def runbot(*args, **kwargs):
    done.append("runbot")
    raise SystemExit(5)
requests.request = runbot
for method in ("add_fork_remote", "prepare_worktree_bundle_folder"):
    setattr(utils.UtilsRunner, method, lambda self, method=method, **kwargs: done.append(method))
sys.argv = ["fetch_bundle.py", name]
try:
    runpy.run_path("fetch_bundle.py", run_name="__main__")
finally:
    print("done:", ",".join(done))
"""
PR = "https://github.com/odoo/odoo/pull/1"
# The head name of the PR, the name fetch_bundle.py is given, its exit code and what it reached.
FETCHES = [
    ("a;b", PR, 1, "done:"),
    ("a$(id)", PR, 1, "done:"),
    ("a,target=x", PR, 1, "done:"),
    ("master-x--seb", PR, 5, "done: runbot"),
    ("", "master-x;id", 1, "done:"),
    ("", "odoo-dev:master-x;id", 1, "done:"),
    ("", "master-x--seb", 5, "done: runbot"),
]
CONTAINERS = {
    "c1": "/src/master/master-x--seb",
    "c2": "/src/master/a;}server{listen 80",
    "c3": "/src/master/b c",
}


def fetch(head, name):
    res = subprocess.run(
        [sys.executable, "-c", FETCH, head, name],
        capture_output=True,
        check=False,
        cwd=HERE,
        text=True,
    )
    return res.returncode, res.stdout.strip().splitlines()[-1]


def fake_podman(command, **kwargs):
    if command[:2] == ["podman", "ps"]:
        out = "\n".join(CONTAINERS)
    elif command[:2] == ["podman", "inspect"] and "--format" in command:
        out = "{}"
    elif command[:2] == ["podman", "inspect"]:
        out = json.dumps([
            {
                "Config": {"Labels": {proxy.DEVCONTAINER_LABEL: folder}},
                "NetworkSettings": {"Networks": {"podman": {"IPAddress": "10.88.0.2"}}},
            }
            for folder in CONTAINERS.values()
        ])
    else:
        out = ""
    return subprocess.CompletedProcess(command, 0, out, "")


def main():
    failures = []
    for head, name, code, reached in FETCHES:
        found = fetch(head, name)
        if found != (code, reached):
            case = f"fetch_bundle {name} with head {head!r}"
            failures.append(f"{case}: {found}, expected {(code, reached)}")
    with tempfile.TemporaryDirectory() as root:
        proxy.CONF_PATH = pathlib.Path(root) / "odoo-dev.conf"
        proxy.subprocess.run = fake_podman
        proxy.regen()
        names = [
            line.split(None, 1)[1].rstrip(";")
            for line in proxy.CONF_PATH.read_text().splitlines()
            if line.strip().startswith("server_name ")
        ]
    expected = [
        "master-x--seb.localhost *.master-x--seb.localhost",
        "bug.master-x--seb.localhost",
        "fix.master-x--seb.localhost",
    ]
    if names != expected:
        failures.append(f"proxy server names: {names}, expected {expected}")
    for line in failures:
        print(line)
    cases = len(FETCHES) + 1
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
