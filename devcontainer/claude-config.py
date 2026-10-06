#!/usr/bin/env python3
"""Prepare the Claude config folder of one bundle container, its CLAUDE_CONFIG_DIR.

    python3 claude-config.py <bundle folder name>     # initializeCommand, on the host

Claude writes its user settings when a model is picked in a tab, and the host must never load
settings a container wrote: they run hooks and commands. So each container gets a folder the host
never reads: a copy of the host's settings.json without its "model", refreshed at every start so a
new session starts on the default model, and links to the rest of ~/.claude as the container mounts
it. session-env and shell-snapshots stay real folders here, as the host's Claude sources its own.
/workspace is marked trusted in the container's .claude.json, or `claude -p` ignores the permissions
of the bundle's .claude. projects is an empty folder the container mounts ~/.claude/projects on,
not a link: Claude checks a write on the path a link resolves to, which takes the memory folder out
of its working directories. No .credentials.json is linked or kept, as a token refresh in a
container revokes the host's login: the container logs in with CLAUDE_CODE_OAUTH_TOKEN.
"""

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import config  # noqa: E402

CONTAINER_CLAUDE = "/home/vscode/.claude"
MOUNTED = {"projects"}
OWN = {"session-env", "settings.json", "shell-snapshots"}
SKIPPED = {".credentials.json", ".git"}


def main():
    values = config.load()
    shared = pathlib.Path(values["HOME"]) / ".claude"
    own = pathlib.Path(values["CACHE_ROOT"]) / "devcontainer" / "claude-config" / sys.argv[1]
    own.mkdir(parents=True, exist_ok=True)

    settings = json.loads((shared / "settings.json").read_text())
    settings.pop("model", None)
    tmp = own / "settings.json.tmp"
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    tmp.replace(own / "settings.json")

    state_path = own / ".claude.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    state.setdefault("projects", {}).setdefault("/workspace", {})["hasTrustDialogAccepted"] = True
    tmp = own / ".claude.json.tmp"
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(state_path)

    (own / ".credentials.json").unlink(missing_ok=True)

    for name in MOUNTED:
        mount_point = own / name
        if mount_point.is_symlink():
            mount_point.unlink()
        mount_point.mkdir(exist_ok=True)

    for entry in shared.iterdir():
        if entry.name in MOUNTED | OWN | SKIPPED:
            continue
        link = own / entry.name
        target = f"{CONTAINER_CLAUDE}/{entry.name}"
        if link.is_symlink():
            if os.readlink(link) == target:
                continue
            link.unlink()
        elif link.exists():
            continue
        link.symlink_to(target)


if __name__ == "__main__":
    main()
