#!/usr/bin/env bash
# Binds ~/.claude/settings.json and ~/.claude/CLAUDE.md on themselves, each with a systemd mount
# unit, see README.md ("Claude").
#   sudo devcontainer/settings-bind.sh            install and start, can run again
#   sudo devcontainer/settings-bind.sh remove
set -euo pipefail

user="${SUDO_USER:-}"
if [[ -z "${user}" || "${EUID}" != 0 ]]; then
	echo "run it with sudo, from the account that owns ~/.claude"
	exit 1
fi
home="$(getent passwd "${user}" | cut -d: -f6)"

for name in CLAUDE.md settings.json; do
	file="$(realpath -m "${home}/.claude/${name}")"
	unit="$(systemd-escape --path --suffix=mount "${file}")"

	if [[ "${1:-}" == remove ]]; then
		systemctl disable --now "${unit}" 2> /dev/null || true
		rm -f "/etc/systemd/system/${unit}"
		systemctl daemon-reload
		echo "removed: ${unit}"
		continue
	fi

	if [[ ! -f "${file}" ]]; then
		echo "${file} is missing: nothing done"
		exit 1
	fi
	cat > "/etc/systemd/system/${unit}" << EOF
[Unit]
Description=Bind of ${file} on itself, which keeps the copy a dev container mounts over it
ConditionPathExists=${file}

[Mount]
What=${file}
Where=${file}
Type=none
Options=bind

[Install]
WantedBy=multi-user.target
EOF
	systemctl daemon-reload
	systemctl enable --now "${unit}"

	# A rename that passes drops the copy of every running container: try it on a bound file only.
	if ! findmnt --noheadings --mountpoint "${file}" > /dev/null; then
		echo "FAILED: ${file} is not bound"
		exit 1
	fi
	probe="${file}.bind-probe"
	cp -p "${file}" "${probe}"
	if mv -T "${probe}" "${file}" 2> /dev/null; then
		echo "FAILED: ${file} can still be replaced by a rename"
		exit 1
	fi
	rm -f "${probe}"
	echo "ok: ${file} is bound on itself, a rename over it is refused"
done
