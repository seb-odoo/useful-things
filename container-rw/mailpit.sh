#!/usr/bin/env bash
# Start the Mailpit of this container once (devcontainer/README.md, "How it runs Odoo"): a served
# Odoo sends its mails to 127.0.0.1:1025, and http://mail.<bundle>.localhost shows them until the
# container stops. Exit 1 on an image without mailpit.
set -u
command -v mailpit >/dev/null || exit 1
pgrep -u "$(id -u)" -x mailpit >/dev/null && exit 0
setsid nohup mailpit --smtp 127.0.0.1:1025 --listen 0.0.0.0:8025 --disable-version-check --quiet \
	>"${TMPDIR:-/tmp}/mailpit.log" 2>&1 </dev/null &
