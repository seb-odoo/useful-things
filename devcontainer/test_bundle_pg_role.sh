#!/usr/bin/env bash
# bundle-pg-role.sh on a real server, in a bundle container (it needs postgres 16 and pgvector):
#   bash test_bundle_pg_role.sh [DB [ODOO_COMMAND...]]
# A cluster made as the image made it before 2026-11-12 (PGUSER superuser, of initdb), with an
# object of each kind and DB copied from the server of the bundle when given, is moved twice;
# then PGUSER must be no superuser, own its databases and their objects, and make and drop a
# database from template_vector. ODOO_COMMAND, when given, runs on the copy of DB, as
# `ODOO_COMMAND -d copy --db_host=... --stop-after-init`.
set -u
role_script=$(dirname "$(readlink -f "$0")")/../container-rw/bundle-pg-role.sh
user=${PGUSER:-$(id -un)}
bin=/usr/lib/postgresql/16/bin
tmp=$(mktemp -d)
trap '$bin/pg_ctl -D "$tmp/16" -m immediate -w stop >/dev/null 2>&1; rm -rf "$tmp"' EXIT
failures=()
fail() { failures+=("$*"); }
q() { PGHOST=$tmp/run psql -X -q -At -v ON_ERROR_STOP=1 "$@"; }

mkdir "$tmp/run"
$bin/initdb -D "$tmp/16" -U "$user" -E UTF8 --locale=C.UTF-8 --lc-collate=C --auth=trust -N >/dev/null || exit 1
$bin/pg_ctl -D "$tmp/16" -o "-k $tmp/run -c listen_addresses=''" -l "$tmp/log" -w start >/dev/null || exit 1
q -d postgres -c "CREATE DATABASE template_vector IS_TEMPLATE true"
q -d template_vector -c "CREATE EXTENSION vector"
q -d postgres -c "CREATE DATABASE kinds TEMPLATE template_vector"
q -d kinds -c "CREATE EXTENSION pg_trgm" -c "CREATE EXTENSION unaccent" \
	-c "CREATE TABLE t (id serial PRIMARY KEY, n int GENERATED ALWAYS AS IDENTITY, v vector(3))" \
	-c "CREATE SEQUENCE alone" -c "CREATE VIEW v AS SELECT id FROM t" \
	-c "CREATE MATERIALIZED VIEW m AS SELECT id FROM t" \
	-c "CREATE FUNCTION f(int) RETURNS int LANGUAGE sql AS 'SELECT \$1'" \
	-c "CREATE AGGREGATE a(int) (SFUNC = int4pl, STYPE = int)" \
	-c "CREATE TYPE e AS ENUM ('x')" -c "CREATE DOMAIN d AS int" -c "CREATE TYPE r AS RANGE (SUBTYPE = int8)" \
	-c "CREATE SCHEMA other" -c "CREATE TABLE other.t2 (id int)"
if [ -n "${1:-}" ]; then
	q -d postgres -c "CREATE DATABASE copy TEMPLATE template0"
	pg_dump -d "$1" -Fc | PGHOST=$tmp/run pg_restore -d copy --no-owner || fail "copy of $1 failed"
fi

for run in first second; do
	start=$SECONDS
	PGHOST=$tmp/run bash "$role_script" || fail "$run run: rc=$?"
	echo "$run run: $((SECONDS - start)) s"
done

[ "$(q -d postgres -c "SELECT rolsuper FROM pg_roles WHERE rolname = '$user'")" = f ] || fail "$user is superuser"
[ "$(q -d postgres -c "SELECT rolname FROM pg_roles WHERE oid = 10")" = postgres ] || fail "the role of initdb is not postgres"
[ -z "$(q -d postgres -c "SELECT rolname FROM pg_roles WHERE rolname = 'bundle_pg_role'")" ] || fail "bundle_pg_role left"
for db in kinds ${1:+copy}; do
	[ "$(q -d postgres -c "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$db'")" = "$user" ] ||
		fail "$db: not owned by $user"
	left=$(q -d "$db" -c "SELECT relname FROM pg_class WHERE relowner = 10 AND oid >= 16384 AND relkind IN ('r','S','v','m')
		AND oid NOT IN (SELECT objid FROM pg_depend WHERE deptype = 'e')
		UNION ALL SELECT proname FROM pg_proc WHERE proowner = 10 AND oid >= 16384
		AND oid NOT IN (SELECT objid FROM pg_depend WHERE deptype = 'e')
		UNION ALL SELECT typname FROM pg_type WHERE typowner = 10 AND oid >= 16384 AND typtype IN ('d','e','r')
		UNION ALL SELECT nspname FROM pg_namespace WHERE nspowner = 10 AND oid >= 16384")
	[ -z "$left" ] || fail "$db: still of the role of initdb: $(echo $left)"
done
q -U "$user" -d kinds -c "INSERT INTO t (v) VALUES ('[1,2,3]')" -c "ALTER TABLE t ADD COLUMN c int" \
	-c "SELECT nextval('alone')" -c "REFRESH MATERIALIZED VIEW m" -c "DROP TABLE other.t2" >/dev/null ||
	fail "$user cannot write or alter what it owns now"
PGHOST=$tmp/run createdb -U "$user" -T template_vector made && PGHOST=$tmp/run dropdb -U "$user" made ||
	fail "$user cannot make and drop a database from template_vector"
if [ -n "${2:-}" ]; then
	shift
	"$@" -d copy --db_host="$tmp/run" --stop-after-init >"$tmp/odoo.log" 2>&1 ||
		fail "odoo rc=$?: $(grep -m3 -E 'ERROR|CRITICAL|superuser' "$tmp/odoo.log")"
	grep -E 'ERROR|CRITICAL' "$tmp/odoo.log" | head -5
	echo "odoo: $(grep -c 'Loading module\|loading.*modules' "$tmp/odoo.log") loading lines, last: $(tail -1 "$tmp/odoo.log" | cut -c1-160)"
fi

printf '%s\n' "${failures[@]}"
echo "bundle-pg-role.sh: ${#failures[@]} problems"
[ ${#failures[@]} = 0 ]
