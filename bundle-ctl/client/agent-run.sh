#!/usr/bin/env bash
# Start the task of an agent window with Claude Code, unattended, and stop it at its first tool call:
# the claude-autoopen extension then opens the Claude tab on the session, which reruns that turn. A
# run that ends without a tool call writes .agent/done instead. Then keep a shell open on its log.
set -u

session="$1"
agent=/workspace/.agent
cd /workspace || exit 1

# VS Code relaunches a persistent terminal's command after a container restart: never run twice.
if ! mkdir "$agent/run.lock" 2>/dev/null; then
	[ -e "$agent/done" ] || [ -e "$agent/handoff" ] || echo interrupted >"$agent/done"
	exec bash -i
fi

# Let Ctrl+C stop the run but not this script, so the tab still opens on the session.
trap : INT
# Record the session as the extension does: the Claude tab hides the ones `claude -p` records.
exec {stream}< <(
	CLAUDE_CODE_ENTRYPOINT=claude-vscode exec claude -p --session-id "$session" -n "$ODOO_PROXY_HOST" \
		--model default --verbose --output-format stream-json <"$agent/task.md"
)
claude=$!
python3 "${0%/*}/stream-format.py" --until-tool-use "$agent/result.md" <&"$stream"
format=$?
exec {stream}<&-
kill -TERM "$claude" 2>/dev/null
wait "$claude"
code=$?
if [ "$format" = 0 ]; then
	: >"$agent/handoff"
else
	echo "$code" >"$agent/done.tmp" && mv "$agent/done.tmp" "$agent/done"
fi
exec bash -i
