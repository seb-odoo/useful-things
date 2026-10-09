#!/usr/bin/env python3
"""Prepare the Claude folders of one bundle container.

    python3 claude-config.py <bundle folder name> [workspace]     # initializeCommand, on the host

The container's CLAUDE_CONFIG_DIR is the host's ~/.claude itself, so it shares the host's login:
Claude locks a token refresh and saves the new token in that folder, and any copy or link of
.credentials.json elsewhere gets revoked by the next refresh. session-env and shell-snapshots are
mounted from a folder of the bundle, as the host's Claude sources its own. So is extension-storage,
where the Claude tab keeps the permission mode of each session: in the container, a rebuild wipes
it and the tab resumes in plan. The workspace (/workspace unless given) is marked trusted in the
.claude.json of that folder, or `claude -p` ignores the permissions of the bundle's .claude. The
onboarding is marked done there too, or the interactive claude of an agent terminal stops on its
theme picker.

What a session takes as orders is mounted read-only in a bundle (see README.md), so what a tab has
to write gets a file of the bundle: settings.json, a copy of the host's where a model picked in a
tab stays, and the project folder, where the shared files are links and settings.local.json is the
bundle's own.
"""

import json
import pathlib
import shutil
import sys

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
    "skills",
)
OWN = ("extension-storage", "project", "session-env", "shell-snapshots")
# Where base.jsonc mounts the shared project folder, read-only.
SHARED_INSIDE = pathlib.Path("/home/vscode/.claude-project")


def write_settings(claude, own):
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
    path.write_text(json.dumps(settings, indent=2) + "\n")


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
    values = config.load()
    claude = pathlib.Path(values["HOME"]) / ".claude"
    own = pathlib.Path(values["CACHE_ROOT"]) / "devcontainer" / "claude-config" / sys.argv[1]
    for name in OWN:
        (own / name).mkdir(parents=True, exist_ok=True)
    for name in (*ORDERS, f"projects/{values['HOST_PROJECT']}"):
        (claude / name).mkdir(parents=True, exist_ok=True)
    (claude / "CLAUDE.md").touch()
    write_settings(claude, own)
    link_project(pathlib.Path(values["SHARED_CLAUDE"]), own / "project")

    state_path = claude / ".claude.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    folder = sys.argv[2] if len(sys.argv) > 2 else "/workspace"
    workspace = state.setdefault("projects", {}).setdefault(folder, {})
    if workspace.get("hasTrustDialogAccepted") and state.get("hasCompletedOnboarding"):
        return
    workspace["hasTrustDialogAccepted"] = True
    state["hasCompletedOnboarding"] = True
    tmp = state_path.with_name(".claude.json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(state_path)


if __name__ == "__main__":
    main()
