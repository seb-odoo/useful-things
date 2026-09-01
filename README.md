# useful-things

The dev environment I work on Odoo with, and the odds and ends that come with it.

| | |
| --- | --- |
| [`devcontainer/`](devcontainer/) | the container every bundle opens, generated from a base and whatever this machine adds to it |
| `Dockerfile`, `devcontainer.bashrc`, `odools.toml` | what that container is built from, and what it sources |
| [`scripts/`](scripts/) | the bundle tooling: fetch a runbot batch, make the worktrees, open the container |
| [`proxy/`](proxy/) | rootless nginx, so parallel containers are reachable by bundle name |
| [`claude-autoopen/`](claude-autoopen/) | a small VS Code extension that opens a Claude tab and a terminal per repo |
| `.bashrc`, `install.sh`, `terminator-config` | the host side of all of it |
| `odoo_*.py`, `populate.sql`, `discuss_populate/`, `compare_logs.py` | helper scripts for odoo work, no setup needed |

Nothing here holds a credential, and no path in a committed file is absolute: the per-machine values
live in `~/.config/odoo-dev/config.env`, described in
[`devcontainer/config.env.example`](devcontainer/config.env.example).
