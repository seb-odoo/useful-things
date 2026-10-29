#!/usr/bin/env python3
"""What a remote agent takes from the laptop: every mount of a bundle has its way in MOUNTS, the
config part holds the config and no login, and the environment leaves out what the worker sets:
python3 test_remote_agent.py

The start, the tunnel, the mirror and the move back run end to end in remote-runs
(`standin/test_standin.py`, `worker/test_agent.py`).
"""

import io
import json
import os
import pathlib
import re
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import remote_agent

failures = []
total = 0


def check(name, passed, detail=""):
    global total
    total += 1
    if not passed:
        failures.append(f"FAIL {name}: {detail}")


def check_mounts():
    text = (HERE.parent / "devcontainer" / "base.jsonc").read_text()
    start = text.index('"mounts": [')
    targets = re.findall(r'"source=[^,]+,target=([^,]+),', text[start : text.index("]", start)])
    missing = [target for target in targets if target not in remote_agent.MOUNTS]
    check("every mount of a bundle has its way to a remote agent", not missing, missing)
    stale = [target for target in remote_agent.MOUNTS if target not in targets]
    check("and the table names no mount that is gone", not stale, stale)
    ways = {way for way, _why in remote_agent.MOUNTS.values()}
    check("each way is one of five", ways <= {"send", "standin", "image", "job", "drop"}, ways)


def check_config(root):
    home = root / "home"
    claude = home / ".claude"
    (claude / "skills" / "a").mkdir(parents=True)
    (claude / "skills" / "a" / "SKILL.md").write_text("skill\n")
    (claude / "CLAUDE.md").write_text("rules\n")
    (claude / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "secret"}}')
    (claude / "skills" / "a" / "out").symlink_to("/etc/passwd")
    own = root / "cache" / "devcontainer" / "claude-config" / "b1"
    own.mkdir(parents=True)
    (own / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "mcpServers": {"x": {"headers": {"Authorization": "t"}}},
    }))
    (own / "settings.json").write_text("{}\n")
    remote_agent.HOME = home
    remote_agent.get_values = lambda: {"CACHE_ROOT": str(root / "cache"),
                                       "REPO_ROOT": str(root / "repo"),
                                       "WORKTREE_ROOT": str(root / "worktrees"),
                                       "HOST_PROJECT": "-home-seb-repo"}
    data = remote_agent.build_config(root / "b1")
    names, contents = [], {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            names.append(member.name)
            if member.isfile():
                contents[member.name] = tar.extractfile(member).read()
    check("the skills go", "home/.claude/skills/a/SKILL.md" in names, names)
    check("the rules go", contents.get("home/.claude/CLAUDE.md") == b"rules\n", names)
    check("the bundle's settings go", "home/.claude/settings.json" in names, names)
    check("never the login", not any("credentials" in name for name in names), names)
    check("nor a link out of the tree", "home/.claude/skills/a/out" not in names, names)
    kept = json.loads(contents["home/.claude/.claude.json"])
    check(".claude.json by its allow list", kept == {"hasCompletedOnboarding": True}, kept)
    check("everything under home/ or workspace/",
          all(name.split("/")[0] in ("home", "workspace") for name in names), names)


def check_env(root):
    config = root / "worktrees" / ".devcontainer" / "devcontainer.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"containerEnv": {
        "BASH_ENV": "/x/devcontainer.bashrc", "HTTPS_PROXY": "http://10.89.255.20:3128",
        "https_proxy": "x", "CLAUDE_CONFIG_DIR": "/home/vscode/.claude", "PGHOST": "/run",
        "GIT_SSH_COMMAND": "/opt/git-gate/git-gate-ssh", "GH_TOKEN": "read-only-via-proxy",
        "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
    }}))
    env = remote_agent.get_env()
    check("the bundle's helpers go", env.get("BASH_ENV") == "/x/devcontainer.bashrc", env)
    check("and what the gateways read", env.get("GH_TOKEN") == "read-only-via-proxy", env)
    left = {"HTTPS_PROXY", "https_proxy", "CLAUDE_CONFIG_DIR", "PGHOST", "GIT_SSH_COMMAND",
            "REQUESTS_CA_BUNDLE"}
    check("not what the worker sets", not left & set(env), env)


def check_remote(root):
    """The bundle writes .agent/remote.json too: a value of another form is no remote agent."""
    agent = root / "bundle" / ".agent"
    agent.mkdir(parents=True)
    good = {"worker": "sebpc", "job": "1761660000", "dummy": "a" * 64, "started": 1.5,
            "session": "8b9b2f6e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"}
    for name, value, expected in [
        ("as the daemon writes it", good, good),
        ("a new session", dict(good, session=""), dict(good, session="")),
        ("a dummy that names a file of Seb", dict(good, dummy="/home/seb/.bashrc"), None),
        ("a dummy that climbs", dict(good, dummy="../../x"), None),
        ("a worker that climbs", dict(good, worker="../mux"), None),
        ("a job with a word", dict(good, job="1 x"), None),
        ("a key unknown here", dict(good, extra="x"), None),
        ("no job", {"worker": "sebpc"}, None),
        ("a list", ["sebpc"], None),
        ("a start at infinity", dict(good, started=float("inf")), None),
    ]:
        (agent / "remote.json").write_text(json.dumps(value))
        found = remote_agent.read_remote(root / "bundle")
        check(f"remote.json {name}", found == expected, found)


def check_standin(root):
    git_dir = root / "repos" / "odoo" / ".git"
    (git_dir / "hooks").mkdir(parents=True)
    (git_dir / "config").write_text("[core]\n")
    remote_agent.get_values = lambda: {"REPO_ROOT": str(root / "repos"), "CACHE_ROOT": str(root)}
    remote_agent.HOME = remote_agent.STATE = root
    remote_agent.REMOTE_RUNS = root / "remote-runs"
    runs = []
    remote_agent.subprocess.run = lambda argv, **kwargs: runs.append(argv)
    remote_agent.start_standin("b", root / "bundle", "w", "")
    volumes = set(runs[-1])
    check("the stand-in mounts the base .git", f"{git_dir}:{git_dir}:rw" in volumes, runs[-1])
    for name in ("config", "hooks"):
        mount = f"{git_dir / name}:{git_dir / name}:ro"
        check(f"and its {name} read-only over it", mount in volumes, runs[-1])
    check("the config stays a file", (git_dir / "config").is_file())


def main():
    check_mounts()
    with tempfile.TemporaryDirectory() as folder:
        root = pathlib.Path(folder)
        check_config(root)
        check_env(root)
        check_remote(root)
        check_standin(root)
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    os.environ.setdefault("REMOTE_RUNS", "/nonexistent")
    main()
