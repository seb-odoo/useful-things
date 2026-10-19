#!/usr/bin/env bash
# Start the postgres server of the bundle, when it has one (devcontainer/README.md, "How it runs
# Odoo"): the postStartCommand of base.jsonc, and agent-run.sh for an agent terminal. A bundle made
# before has no ~/.local/share/postgresql/16 and keeps the host's server.
set -u
pg=$HOME/.local/share/postgresql
data=$pg/16
[ -d "$data" ] || exit 0
exec 9>"$pg/.lock"
flock 9
pgrep -u "$(id -u)" -x postgres >/dev/null && exit 0
if [ ! -e "$data/PG_VERSION" ]; then
	rm -rf "$pg/16.copy"
	cp -a /opt/bundle-pg/16 "$pg/16.copy" && rmdir "$data" && mv "$pg/16.copy" "$data" || exit 1
fi
chmod 700 "$data"
# Killed with its container: the pid in its files may be another process of this container now.
rm -f "$data/postmaster.pid" /var/run/postgresql/.s.PGSQL.5432 /var/run/postgresql/.s.PGSQL.5432.lock
/usr/lib/postgresql/16/bin/pg_ctl -D "$data" -l "$pg/server.log" -w start >/dev/null 9>&-
