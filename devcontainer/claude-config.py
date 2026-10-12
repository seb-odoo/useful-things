#!/usr/bin/env python3
"""Prepare the Claude folders of one bundle container.

    python3 claude-config.py <bundle folder name> [workspace]     # initializeCommand, on the host

The container's CLAUDE_CONFIG_DIR is the host's ~/.claude itself, so it shares the host's login:
Claude locks a token refresh and saves the new token in that folder, and any copy or link of
.credentials.json elsewhere gets revoked by the next refresh. session-env and shell-snapshots are
mounted from a folder of the bundle, as the host's Claude sources its own. So is sessions, where
Claude names the record of a live session after its pid: the claude of every container has the
same few pids, so in a shared folder the records overwrite each other. So is extension-storage,
where the Claude tab keeps the permission mode of each session: in the container, a rebuild wipes
it and the tab resumes in plan. So is .claude.json, the state of Claude, where an MCP server is
kept: one file for every container would carry a server added in one into the others. It starts
as a copy of the host's, with the workspace (/workspace unless given) marked trusted, or
`claude -p` ignores the permissions of the bundle's .claude, and the onboarding marked done, or
the interactive claude of an agent terminal stops on its theme picker.

What a session takes as orders is mounted read-only in a bundle (see README.md), so what a tab has
to write gets a file of the bundle: settings.json, a copy of the host's where a model picked in a
tab stays, the other files Claude writes there (COPIES), and the project folder, where the shared
files are links and settings.local.json is the bundle's own. What Claude reads in ~/.claude as the
project folder of the home (HOME_PROJECT) is made on the host when missing, for a read-only mount.
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
    "routines",
    "rules",
    "skills",
    "workflows",
)
OWN = ("extension-storage", "project", "session-env", "sessions", "shell-snapshots")
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
    values = config.load()
    claude = pathlib.Path(values["HOME"]) / ".claude"
    own = pathlib.Path(values["CACHE_ROOT"]) / "devcontainer" / "claude-config" / sys.argv[1]
    for name in OWN:
        if (own / name).is_symlink():
            (own / name).unlink()
        (own / name).mkdir(parents=True, exist_ok=True)
    for name in (*ORDERS, f"projects/{values['HOST_PROJECT']}"):
        (claude / name).mkdir(parents=True, exist_ok=True)
    (claude / "CLAUDE.md").touch()
    write_settings(claude, own)
    make_files(claude, own)
    link_project(pathlib.Path(values["SHARED_CLAUDE"]), own / "project")

    state_path = own / ".claude.json"
    seed = claude / ".claude.json"
    state = {}
    for path in (state_path, seed):
        if path.is_file():
            state = json.loads(path.read_text())
            break
    folder = sys.argv[2] if len(sys.argv) > 2 else "/workspace"
    workspace = state.setdefault("projects", {}).setdefault(folder, {})
    workspace["hasTrustDialogAccepted"] = True
    state["hasCompletedOnboarding"] = True
    # In place: the file is bound into a running container, and a rename would leave it behind.
    state_path.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
