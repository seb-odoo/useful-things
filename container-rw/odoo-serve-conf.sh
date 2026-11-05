#!/usr/bin/env bash
# Print the path of the config a served Odoo of this bundle runs with (devcontainer/README.md, "How
# it runs Odoo"): its master password, random, made at the first call and kept in the bundle
# folder. With --password, print that password.
set -eu
config=/workspace/.odoo-serve.conf
if [[ ! -s "${config}" ]]; then
	umask 077
	password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
	printf '[options]\nadmin_passwd = %s\n' "${password}" >"${config}.new"
	mv "${config}.new" "${config}"
fi
if [[ "${1:-}" == --password ]]; then
	sed -n 's/^admin_passwd = //p' "${config}"
else
	echo "${config}"
fi
