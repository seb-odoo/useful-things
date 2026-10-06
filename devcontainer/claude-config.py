#!/usr/bin/env python3
"""Prepare the Claude folders of one bundle container.

    python3 claude-config.py <bundle folder name>     # initializeCommand, on the host

The container's CLAUDE_CONFIG_DIR is the host's ~/.claude itself, so it shares the host's login:
Claude locks a token refresh and saves the new token in that folder, and any copy or link of
.credentials.json elsewhere gets revoked by the next refresh. session-env and shell-snapshots are
mounted from a folder of the bundle, as the host's Claude sources its own. So is extension-storage,
where the Claude tab keeps the permission mode of each session: in the container, a rebuild wipes
it and the tab resumes in plan. /workspace is marked trusted in the .claude.json of that folder, or
`claude -p` ignores the permissions of the bundle's .claude.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import config  # noqa: E402

OWN = ("extension-storage", "session-env", "shell-snapshots")


def main():
    values = config.load()
    own = pathlib.Path(values["CACHE_ROOT"]) / "devcontainer" / "claude-config" / sys.argv[1]
    for name in OWN:
        (own / name).mkdir(parents=True, exist_ok=True)

    state_path = pathlib.Path(values["HOME"]) / ".claude" / ".claude.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    workspace = state.setdefault("projects", {}).setdefault("/workspace", {})
    if workspace.get("hasTrustDialogAccepted"):
        return
    workspace["hasTrustDialogAccepted"] = True
    tmp = state_path.with_name(".claude.json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(state_path)


if __name__ == "__main__":
    main()
