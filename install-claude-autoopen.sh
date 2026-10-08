#!/usr/bin/env bash
# Install the in-house "claude-autoopen" extension into this dev container's VS Code server when the
# bundled .vsix changed, so edits to the bundled .vsix propagate to already-built containers.
# The extension opens a Claude Code tab and one terminal per repo on window open, and runs this
# script itself then. `code` is not on PATH there and the server lives under /vscode here, so locate
# the remote CLI and the window's IPC socket explicitly, then install through the running server
# (correct extensions dir).
set -u

vsix="$HOME/.claude-autoopen/claude-autoopen-0.0.1.vsix"
installed="$HOME/.vscode-server/extensions/local.claude-autoopen-0.0.1/extension.js"

# Skip the install when the code is the same. Only extension.js compares: the install adds a
# __metadata key to its package.json.
python3 - "$vsix" <<'EOF' 2>/dev/null | cmp -s - "$installed" && exit 0
import sys, zipfile
sys.stdout.buffer.write(zipfile.ZipFile(sys.argv[1]).read("extension/extension.js"))
EOF

code_bin=$(ls -t /vscode/vscode-server/bin/linux-x64/*/bin/remote-cli/code \
  "$HOME"/.vscode-server/bin/*/bin/remote-cli/code 2>/dev/null | head -n1)
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
