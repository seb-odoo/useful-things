#!/usr/bin/env bash
# Give PGUSER the role of the image on a cluster copied before 2026-11-12 (devcontainer/README.md,
# "How it runs Odoo"): Odoo master refuses a superuser, and PostgreSQL keeps the role of initdb
# superuser. That role becomes `postgres`, and PGUSER a new role with CREATEDB, owner of every
# database but the system ones and template_vector, and of what they hold. Run by bundle-pg.sh
# after a start, and by agent.sh of remote-runs. On a cluster done already, it only reads.
set -eu -o pipefail
user=${PGUSER:-$(id -un)}
export PGOPTIONS="-c client_min_messages=warning"
sql() { psql -X -q -At -v ON_ERROR_STOP=1 "$@"; }

if [ "$(sql -U "$user" -d postgres -c "SELECT oid FROM pg_roles WHERE rolname = current_user")" = 10 ]; then
	sql -U "$user" -d postgres -c "DROP ROLE IF EXISTS bundle_pg_role" -c "CREATE ROLE bundle_pg_role SUPERUSER LOGIN"
	sql -U bundle_pg_role -d postgres -1 -c "ALTER ROLE \"$user\" RENAME TO postgres" \
		-c "CREATE ROLE \"$user\" LOGIN CREATEDB"
	sql -U postgres -d postgres -c "DROP ROLE bundle_pg_role"
fi

# Objects made after initdb (oid 16384 and up), but those of an extension and the sequences of a
# column, which follow their owner.
moves="
SELECT format('ALTER %s %s OWNER TO %I;', CASE c.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
    WHEN 'S' THEN 'SEQUENCE' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END, c.oid::regclass, :'user')
FROM pg_class c
WHERE c.relowner = 10 AND c.oid >= 16384 AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
    AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid
        AND (d.deptype = 'e' OR c.relkind = 'S' AND d.deptype IN ('a', 'i')))
UNION ALL
SELECT format('ALTER %s %s OWNER TO %I;', CASE p.prokind WHEN 'a' THEN 'AGGREGATE' WHEN 'p' THEN 'PROCEDURE'
    ELSE 'FUNCTION' END, p.oid::regprocedure, :'user')
FROM pg_proc p
WHERE p.proowner = 10 AND p.oid >= 16384
    AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
UNION ALL
SELECT format('ALTER SCHEMA %I OWNER TO %I;', n.nspname, :'user')
FROM pg_namespace n
WHERE n.nspowner = 10 AND n.oid >= 16384
    AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid = 'pg_namespace'::regclass AND d.objid = n.oid AND d.deptype = 'e')
UNION ALL
SELECT format('ALTER %s %s OWNER TO %I;', CASE t.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END, t.oid::regtype, :'user')
FROM pg_type t
WHERE t.typowner = 10 AND t.oid >= 16384 AND t.typtype IN ('d', 'e', 'r')
    AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e')
"
sql -U postgres -d postgres -c "SELECT datname FROM pg_database WHERE datdba = 10
	AND datname NOT IN ('postgres', 'template0', 'template1', 'template_vector')" | while IFS= read -r db; do
	sql -U postgres -d "$db" -v user="$user" <<<"$moves" | sql -U postgres -d "$db"
	sql -U postgres -d postgres -c "ALTER DATABASE \"$db\" OWNER TO \"$user\""
done
