#!/usr/bin/env bash
# Claude Code SessionStart hook of the bundles: say when the open PR of this bundle targets another
# base than the bundle, and how to move the work there. Its output becomes session context.
set -u

bundle="${ODOO_PROXY_HOST:-}"
case "$bundle" in
*--seb) ;;
*) exit 0 ;;
esac
case "$bundle" in
saas-*) base=$(echo "$bundle" | cut -d- -f1-2) ;;
*) base="${bundle%%-*}" ;;
esac
name="${bundle#"$base"-}"
name="${name%--seb}"

for repo in odoo/odoo odoo/enterprise; do
	target=$(timeout 10 gh pr list --repo "$repo" --head "$bundle" --state open \
		--json baseRefName --jq '.[0].baseRefName // empty' 2>/dev/null)
	if [ -n "$target" ] && [ "$target" != "$base" ]; then
		echo "The open PR of $bundle in $repo targets $target, but this bundle is based on $base." \
			"To move the work, run \`bctl create $target $name --task-file <file>\` (Bash timeout" \
			"600000) with a task asking to cherry-pick the commits $bundle has over $base: it" \
			"opens $target-$name--seb with an agent on that task."
		exit 0
	fi
done
