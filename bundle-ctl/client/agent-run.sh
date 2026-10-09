#!/usr/bin/env bash
# Start the task of an agent with Claude Code.
#
#   agent-run.sh --terminal SESSION [PROMPT]
#                                     in a terminal (agent-terminal.py), no VS Code: interactive.
#                                     A run that already started is not run again: its session
#                                     opens as it was, on PROMPT when one is given.
#   agent-run.sh SESSION              in an agent window, unattended, stopped at its first tool call:
#                                     the claude-autoopen extension then opens the Claude tab on the
#                                     session, which reruns that turn. A run that ends without a tool
#                                     call writes .agent/done instead. Then keep a shell on its log.
set -u

terminal=
if [ "$1" = --terminal ]; then
	terminal=1
	shift
fi
session="$1"
agent=/workspace/.agent
cd /workspace || exit 1

# Run on opus unless the task asked for haiku or sonnet: the bundle can write this file.
case $(cat "$agent/model" 2>/dev/null) in
haiku) model=haiku ;;
sonnet) model=sonnet ;;
*) model=opus ;;
esac

# VS Code relaunches a persistent terminal's command after a container restart: never run twice.
reopen=
if ! mkdir "$agent/run.lock" 2>/dev/null; then
	if [ -z "$terminal" ]; then
		[ -e "$agent/done" ] || [ -e "$agent/handoff" ] || echo interrupted >"$agent/done"
		exec bash -i
	fi
	reopen=1
fi

# Every container shares ~/.claude: starts at the same time fail each other's token refresh.
space_starts() {
	flock "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agent-start.lock" sleep 10
}

if [ -n "$terminal" ]; then
	space_starts
	start=(--session-id "$session")
	if [ -e "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/-workspace/$session.jsonl" ]; then
		start=(--resume "$session")
	fi
	if [ -n "$reopen" ]; then
		mode=$(cat "$agent/mode" 2>/dev/null)
		exec claude "${start[@]}" -n "$ODOO_PROXY_HOST" --model "$model" \
			--permission-mode "${mode:-auto}" ${2:+-- "$2"}
	fi
	# The mode `claude -p` runs in: the Claude tab does not restore the bundles' default, dontAsk.
	exec claude "${start[@]}" -n "$ODOO_PROXY_HOST" --model "$model" \
		--permission-mode auto -- "$(cat "$agent/task.md")"
fi

# Let Ctrl+C stop the run but not this script, so the tab still opens on the session.
trap : INT
retry=0
while :; do
	space_starts
	# Record the session as the extension does: the Claude tab hides the ones `claude -p` records.
	exec {stream}< <(
		CLAUDE_CODE_ENTRYPOINT=claude-vscode exec claude -p --session-id "$session" -n "$ODOO_PROXY_HOST" \
			--model "$model" --verbose --output-format stream-json <"$agent/task.md"
	)
	claude=$!
	python3 "${0%/*}/stream-format.py" --until-tool-use "$agent/result.md" <&"$stream"
	format=$?
	exec {stream}<&-
	kill -TERM "$claude" 2>/dev/null
	wait "$claude"
	code=$?
	if [ "$format" = 0 ] || [ "$retry" = 3 ] ||
		! grep -qE '^Failed to (refresh OAuth token|authenticate)' "$agent/result.md"; then
		break
	fi
	retry=$((retry + 1))
	echo "auth failed, retry $retry/3" >"$agent/retry"
	sleep $((60 + RANDOM % 61))
	session=$(cat /proc/sys/kernel/random/uuid)
	echo "$session" >"$agent/session"
done
if [ "$format" = 0 ]; then
	rm -f "$agent/retry"
	: >"$agent/handoff"
else
	echo "$code" >"$agent/done.tmp" && mv "$agent/done.tmp" "$agent/done"
fi
exec bash -i
