#!/usr/bin/env bash
# Run the task of an agent window with Claude Code, unattended, then keep a shell open on its log.
# The claude-autoopen extension starts it in the "agent" terminal with the session id it chose, and
# opens the Claude tab on that session once .agent/done appears.
set -u

session="$1"
agent=/workspace/.agent
cd /workspace || exit 1

# VS Code relaunches a persistent terminal's command after a container restart: never run twice.
if ! mkdir "$agent/run.lock" 2>/dev/null; then
	[ -e "$agent/done" ] || echo interrupted >"$agent/done"
	exec bash -i
fi

claude -p --session-id "$session" -n "$ODOO_PROXY_HOST" --verbose --output-format stream-json \
	<"$agent/task.md" | python3 "${0%/*}/stream-format.py" "$agent/result.md"
echo "${PIPESTATUS[0]}" >"$agent/done.tmp" && mv "$agent/done.tmp" "$agent/done"
exec bash -i
