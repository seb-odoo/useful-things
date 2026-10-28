#!/usr/bin/env python3
"""What claude-config.py makes for a bundle and for the ops container, in a throwaway home:
python3 test_claude_config.py"""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402

BUNDLE = "master-probe--seb"
REAL = {"accessToken": "sk-ant-oat01-real", "refreshToken": "sk-ant-ort01-real"}
results = []


def check(name, got, expected):
    results.append(got == expected)
    if got != expected:
        print(f"FAIL {name}: got {got!r}, expected {expected!r}")


def run(root, *args):
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(root / "home"),
        "CACHE_ROOT": str(root / "cache"),
        "CONFIG_ROOT": str(root / "config"),
        "SHARE_ROOT": str(root / "share"),
        "STATE_ROOT": str(root / "state"),
        "SHARED_CLAUDE": str(root / "shared"),
        "REPO_ROOT": "/home/seb/repo",
    }
    return subprocess.run(
        [sys.executable, "-B", str(HERE / "claude-config.py"), *args],
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )


def read_login(own):
    return json.loads((own / "home" / ".credentials.json").read_text())["claudeAiOauth"]


def check_bundle(root):
    claude = root / "home" / ".claude"
    claude.mkdir(parents=True)
    real = {**REAL, "subscriptionType": "team", "rateLimitTier": "tier"}
    (claude / ".credentials.json").write_text(json.dumps({"claudeAiOauth": real}))
    (claude / "settings.json").write_text('{"model": "opus"}\n')
    res = run(root, BUNDLE, "--bundle")
    check("a bundle: exit code", (res.returncode, res.stderr), (0, ""))
    own = root / "cache" / "devcontainer" / "claude-config" / BUNDLE
    login = read_login(own)
    check("a dummy login in its folder", login["accessToken"].startswith("sk-ant-oat01-rr-"), True)
    check("nothing of the host's", REAL["refreshToken"] in json.dumps(login), False)
    plan = (login["subscriptionType"], login["rateLimitTier"])
    check("of the plan of the host", plan, ("team", "tier"))
    mode = (own / "home" / ".credentials.json").stat().st_mode & 0o777
    check("for its owner only", oct(mode), "0o600")
    name = hashlib.sha256(login["accessToken"].encode()).hexdigest()
    named = root / "state" / "claude-switch" / "dummies" / name
    check("named for the switch", named.is_file() and named.read_text(), f"{BUNDLE}\n")
    settings = json.loads((own / "settings.json").read_text())
    check("no claude.ai connector", settings.get("disableClaudeAiConnectors"), True)
    for path in (
        "share/claude-plans/dev",
        "home/.claude/projects/-workspace",
        "home/.claude/projects/-home-seb-repo/memory",
    ):
        check(f"mount source {path}", (root / path).is_dir(), True)
    check("mount source of the lock", (claude / "agent-start.lock").is_file(), True)

    run(root, BUNDLE, "--bundle")
    check("the same dummy at the next start", read_login(own)["accessToken"], login["accessToken"])
    (own / "home" / ".credentials.json").write_text(json.dumps({"claudeAiOauth": REAL}))
    run(root, BUNDLE, "--bundle")
    check("a /login in the bundle is undone", read_login(own)["accessToken"], login["accessToken"])
    (own / "home" / ".credentials.json").unlink()
    target = root / "target"
    target.write_text("kept\n")
    (own / "home" / ".credentials.json").symlink_to(target)
    run(root, BUNDLE, "--bundle")
    check("a link left there is not written through", target.read_text(), "kept\n")
    check("but replaced", (own / "home" / ".credentials.json").is_symlink(), False)


def check_ops(root):
    (root / "home" / ".claude").mkdir(parents=True)
    (root / "home" / ".claude" / "settings.json").write_text("{}\n")
    res = run(root, "ops", str(root / "repo"))
    check("the ops container: exit code", (res.returncode, res.stderr), (0, ""))
    own = root / "cache" / "devcontainer" / "claude-config" / "ops"
    check("no folder of its own: the host's login", (own / "home").exists(), False)
    settings = json.loads((own / "settings.json").read_text())
    check("its connectors stay", "disableClaudeAiConnectors" in settings, False)


def check_mounts():
    mounts = build.read_jsonc(HERE / "base.jsonc")["mounts"]
    targets = {
        mount.split("target=")[1].split(",")[0]: mount.split("source=")[1].split(",")[0]
        for mount in mounts
    }
    folder = "@CACHE_ROOT@/devcontainer/claude-config/${localWorkspaceFolderBasename}/home"
    check("the Claude folder of a bundle is its own", targets.get("/home/vscode/.claude"), folder)
    plans = targets.get("/home/vscode/.claude/plans")
    check("its plans, the dev ones", plans, "@SHARE_ROOT@/claude-plans/dev")
    host = ("@HOME@/.claude", "@HOME@/.claude/plans", "@HOME@/.claude/projects")
    check("no mount of the host's folder or plans", [s for s in targets.values() if s in host], [])


def main():
    with tempfile.TemporaryDirectory() as name:
        check_bundle(pathlib.Path(name))
    with tempfile.TemporaryDirectory() as name:
        check_ops(pathlib.Path(name))
    check_mounts()
    print(f"{sum(results)}/{len(results)} checks as expected")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
