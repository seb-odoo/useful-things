#!/usr/bin/env python3
"""Prepare the Claude folders of one bundle container, or of the ops container.

    python3 claude-config.py <folder name> --bundle       # initializeCommand of a bundle, on the host
    python3 claude-config.py <folder name> <workspace>    # the one of the ops container

The CLAUDE_CONFIG_DIR of the ops container is the host's ~/.claude itself, so it shares the host's
login: Claude locks a token refresh and saves the new token in that folder, and any copy or link of
.credentials.json elsewhere gets revoked by the next refresh. A bundle has a folder of its own
there instead (home), where what it shares of the host's is mounted (base.jsonc), with a dummy
login that the Claude switch sends with the main one (gateway-core README, "The Claude switch"):
its claude has no credential to give away, and loads no claude.ai connector. The dummy is kept out
of the bundle's reach (dummy-login), and written again at each start over what a /login there
left. session-env and shell-snapshots are
mounted from a folder of the bundle, as the host's Claude sources its own. So is sessions, where
Claude names the record of a live session after its pid: the claude of every container has the
same few pids, so in a shared folder the records overwrite each other. So is extension-storage,
where the Claude tab keeps the permission mode of each session: in the container, a rebuild wipes
it and the tab resumes in plan. So is .claude.json, the state of Claude, where an MCP server is
kept: one file for every container would carry a server added in one into the others. It starts
as a copy of the host's, with the workspace (/workspace unless given) marked trusted, or
`claude -p` ignores the permissions of the bundle's .claude, its imports from outside allowed, or
an agent stops on the question for the runbot-fix-agents CLAUDE.md that the bundle CLAUDE.md
imports, and the onboarding marked done, or the interactive claude of an agent terminal stops on
its theme picker. So are what Claude keeps for itself and reads again later (backups, cache,
file-history, ide) and dev-mods, whose mods run in the Claude process of their session. So are gh
and ssh, with the fake GitHub token and SSH key of the bundle (fake-keys.json, made once): no
tool of a bundle sends them, and the gateways and git-gate, which read them in
STATE_ROOT/gateway-callers/fake, hold a card when a request carries one.

What a session takes as orders is mounted read-only in a bundle (see README.md), so what a tab has
to write gets a file of the bundle: settings.json, a copy of the host's where a model picked in a
tab stays, the other files Claude writes there (COPIES), and the project folder, where the shared
files are links and settings.local.json is the bundle's own. What Claude reads in ~/.claude as the
project folder of the home (HOME_PROJECT) is made on the host when missing, for a read-only mount.
"""

import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import config  # noqa: E402

ORDERS = (
    "agents",
    "bin",
    "commands",
    "external",
    "hooks",
    "output-styles",
    "plugins",
    "review-corpus",
    "routines",
    "rules",
    "skills",
    "workflows",
)
OWN = (
    "backups",
    "cache",
    "dev-mods",
    "extension-storage",
    "file-history",
    "gh",
    "ide",
    "project",
    "session-env",
    "sessions",
    "shell-snapshots",
    "ssh",
)
# Files Claude writes in its config folder: a copy per container, as the host's Claude reads them.
COPIES = (
    "daemon.json",
    "policy-limits.json",
    "remote-settings-helper-consent",
    "remote-settings.json",
)
# What Claude reads in ~/.claude as the project folder of the home: read-only in a container.
HOME_PROJECT = {
    "launch.json": "{}\n",
    "loop.md": "",
    "scheduled_tasks.json": '{"tasks": []}\n',
    "settings.local.json": "{}\n",
}
# Where base.jsonc mounts the shared project folder, read-only.
SHARED_INSIDE = pathlib.Path("/home/vscode/.claude-project")
DUMMY = "sk-ant-oat01-rr-"
# What the Claude switch serves a dummy login: it says it has these scopes only.
DUMMY_SCOPES = ["user:inference", "user:profile", "user:sessions:claude_code"]
BASE62 = string.digits + string.ascii_uppercase + string.ascii_lowercase


def make_token():
    """A token shaped as a classic one of GitHub, its checksum included."""
    body = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(30))
    crc, digits = zlib.crc32(body.encode()), ""
    while crc:
        crc, rest = divmod(crc, 62)
        digits = BASE62[rest] + digits
    return f"ghp_{body}{digits.rjust(6, '0')}"


def make_key():
    with tempfile.TemporaryDirectory() as folder:
        path = pathlib.Path(folder, "key")
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(path)],
            capture_output=True,
            check=True,
        )
        return path.read_text(), path.with_name("key.pub").read_text()


def write_file(path, text, mode=0o644):
    """Write a file of a folder the bundle writes, never through a link it left there."""
    if path.is_symlink():
        path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w") as file:
        file.write(text)


def get_login(values):
    """The GitHub login of the host's gh, read from its config and never its token."""
    try:
        text = (pathlib.Path(values["HOME"]) / ".config" / "gh" / "hosts.yml").read_text()
    except OSError:
        text = ""
    found = re.search(r"^\s+user: ([\w-]+)$", text, re.MULTILINE)
    return found[1] if found else "vscode"


def write_fakes(own, name, values):
    """The fake GitHub token and SSH key of the bundle, which no tool of it sends: named for the
    gateways and git-gate, which refuse a request that carries one and hold a card in hok."""
    try:
        fakes = json.loads((own / "fake-keys.json").read_text())
        token, key, public = fakes["token"], fakes["key"], fakes["public"]
    except (OSError, ValueError, KeyError, TypeError):
        token, (key, public) = make_token(), make_key()
        fakes = {"token": token, "key": key, "public": public}
        (own / "fake-keys.json").write_text(json.dumps(fakes) + "\n")
    login = get_login(values)
    write_file(
        own / "gh" / "hosts.yml",
        f"github.com:\n    users:\n        {login}:\n            oauth_token: {token}\n"
        f"    git_protocol: https\n    oauth_token: {token}\n    user: {login}\n",
        0o600,
    )
    write_file(own / "ssh" / "id_ed25519", key, 0o600)
    write_file(own / "ssh" / "id_ed25519.pub", public)
    # The first line of an ed25519 key is the same for every key.
    lines = key.splitlines()[2:-1]
    folder = pathlib.Path(values["STATE_ROOT"]) / "gateway-callers" / "fake"
    folder.mkdir(parents=True, exist_ok=True)
    fresh = folder / f".{name}.json"
    fresh.write_text(json.dumps({"bundle": name, "token": token, "key": lines}) + "\n")
    fresh.replace(folder / f"{name}.json")
    return token


def write_settings(claude, own, bundle, token=None):
    path = own / "settings.json"
    if not (claude / "settings.json").is_file():
        path.touch()
        return
    settings = json.loads((claude / "settings.json").read_text())
    try:
        picked = json.loads(path.read_text()).get("model")
    except (OSError, ValueError):
        picked = None
    settings.pop("model", None)
    if picked:
        settings["model"] = picked
    # An ask rule prompts even when a hook allowed the call, and in a container the hooks decide.
    settings.get("permissions", {}).pop("ask", None)
    settings.setdefault("claudeMdExcludes", []).append(str(claude / "CLAUDE.md"))
    if bundle:
        settings["disableClaudeAiConnectors"] = True
    if token:
        settings.setdefault("env", {})["GITHUB_PAT"] = token
    path.write_text(json.dumps(settings, indent=2) + "\n")


def write_login(claude, own, name, values):
    """The dummy login of the bundle, named for the Claude switch, in its folder of its own."""
    home = own / "home"
    if home.is_symlink():
        home.unlink()
    home.mkdir(exist_ok=True)
    try:
        dummy = (own / "dummy-login").read_text().strip()
    except OSError:
        dummy = f"{DUMMY}{secrets.token_urlsafe(36)}"
        (own / "dummy-login").write_text(f"{dummy}\n")
    dummies = pathlib.Path(values["STATE_ROOT"]) / "claude-switch" / "dummies"
    dummies.mkdir(parents=True, exist_ok=True)
    (dummies / hashlib.sha256(dummy.encode()).hexdigest()).write_text(f"{name}\n")
    try:
        real = json.loads((claude / ".credentials.json").read_text())["claudeAiOauth"]
    except (OSError, ValueError, KeyError, TypeError):
        real = {}
    login = {
        "accessToken": dummy,
        "refreshToken": "sk-ant-ort01-rr-dummy",
        "expiresAt": 4102444800000,
        "scopes": DUMMY_SCOPES,
        "subscriptionType": real.get("subscriptionType") or "max",
    }
    if real.get("rateLimitTier"):
        login["rateLimitTier"] = real["rateLimitTier"]
    path = home / ".credentials.json"
    if path.is_symlink():
        path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(json.dumps({"claudeAiOauth": login}) + "\n")


def make_files(claude, own):
    """The files a container mounts over the host's: a mount needs a source, and a file the host
    has not got yet is made here rather than left empty by podman."""
    for name, text in HOME_PROJECT.items():
        if not (claude / name).exists():
            (claude / name).write_text(text)
    for name in COPIES:
        if not (claude / name).exists():
            (claude / name).touch()
        if (own / name).is_symlink():
            (own / name).unlink()
        if not (own / name).exists():
            (own / name).write_bytes((claude / name).read_bytes())


def link_project(shared, project):
    for path in project.iterdir():
        if path.is_symlink():
            path.unlink()
    for path in shared.iterdir() if shared.is_dir() else ():
        if path.name == "settings.local.json":
            if not (project / path.name).exists():
                shutil.copy(path, project / path.name)
        elif path.suffix != ".lock":
            (project / path.name).symlink_to(SHARED_INSIDE / path.name)


def main():
    bundle = "--bundle" in sys.argv
    args = [arg for arg in sys.argv[1:] if arg != "--bundle"]
    values = config.load()
    claude = pathlib.Path(values["HOME"]) / ".claude"
    own = pathlib.Path(values["CACHE_ROOT"]) / "devcontainer" / "claude-config" / args[0]
    for name in OWN:
        if (own / name).is_symlink():
            (own / name).unlink()
        (own / name).mkdir(parents=True, exist_ok=True)
    shared = (*ORDERS, f"handoff/{args[0]}", f"projects/{values['HOST_PROJECT']}/memory")
    for name in (*shared, "projects/-workspace"):
        (claude / name).mkdir(parents=True, exist_ok=True)
    (claude / "CLAUDE.md").touch()
    (claude / "agent-start.lock").touch()
    (pathlib.Path(values["SHARE_ROOT"]) / "claude-plans" / "dev").mkdir(parents=True, exist_ok=True)
    token = write_fakes(own, args[0], values) if bundle else None
    write_settings(claude, own, bundle, token)
    if bundle:
        write_login(claude, own, args[0], values)
    make_files(claude, own)
    link_project(pathlib.Path(values["SHARED_CLAUDE"]), own / "project")

    state_path = own / ".claude.json"
    seed = claude / ".claude.json"
    state = {}
    for path in (state_path, seed):
        if path.is_file():
            state = json.loads(path.read_text())
            break
    folder = args[1] if len(args) > 1 else "/workspace"
    workspace = state.setdefault("projects", {}).setdefault(folder, {})
    workspace["hasTrustDialogAccepted"] = True
    workspace["hasClaudeMdExternalIncludesApproved"] = True
    workspace["hasClaudeMdExternalIncludesWarningShown"] = True
    state["hasCompletedOnboarding"] = True
    # In place: the file is bound into a running container, and a rename would leave it behind.
    state_path.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
