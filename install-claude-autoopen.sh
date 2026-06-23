#!/usr/bin/env bash
# Install (force, idempotent) the in-house "claude-autoopen" extension into this dev container's VS
# Code server on every attach, so edits to the bundled .vsix propagate to already-built containers.
# The extension opens a Claude Code tab and one terminal per repo on window open. `code` is not on
# PATH in lifecycle hooks and the server lives under /vscode here, so locate the remote CLI and the
# window's IPC socket explicitly, then install through the running server (correct extensions dir).
set -u

vsix="$HOME/.claude-autoopen.vsix"

code_bin=$(find /vscode "$HOME" -maxdepth 8 -path '*remote-cli/code' -type f -printf '%T@ %p\n' 2>/dev/null \
  | sort -nr | head -n1 | cut -d' ' -f2-)
[ -z "$code_bin" ] && exit 0

# The hook-provided VSCODE_IPC_HOOK_CLI can point to an already-closed server connection
# (ECONNREFUSED), so try it first then fall back to any live /tmp socket (newest first) rather than
# failing the whole lifecycle command. Never exit non-zero: the extension is also covered by the
# persistent extensions cache, so a one-off hook miss self-heals on the next attach.
for sock in "${VSCODE_IPC_HOOK_CLI:-}" $(ls -t /tmp/vscode-ipc-*.sock 2>/dev/null); do
  [ -S "$sock" ] || continue
  export VSCODE_IPC_HOOK_CLI="$sock"
  # Force-install so a rebuilt same-version .vsix overwrites the cached copy (idempotent).
  "$code_bin" --install-extension "$vsix" --force >/dev/null 2>&1 && exit 0
done
exit 0
