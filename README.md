# useful-things

The dev environment I work on Odoo with, and the odds and ends that come with it.

| | |
| --- | --- |
| [`devcontainer/`](devcontainer/) | the container every bundle opens, generated from a base and whatever this machine adds to it |
| `Dockerfile`, `container-rw/`, `odools.toml` | what that container is built from, and what it sources |
| [`scripts/`](scripts/) | the bundle tooling: fetch a runbot batch, make the worktrees, open the container |
| [`bundle-ctl/`](bundle-ctl/) | a host daemon, so an agent in a container can create, fetch and open bundles |
| [`proxy/`](proxy/) | rootless nginx, so parallel containers are reachable by bundle name |
| [`claude-autoopen/`](claude-autoopen/) | a small VS Code extension that opens a Claude tab and a terminal per repo |
| `.bashrc`, `install.sh`, `terminator-config` | the host side of all of it |
| `odoo_*.py`, `populate.sql`, `discuss_populate/`, `compare_logs.py` | helper scripts for odoo work, no setup needed |

Nothing here holds a credential. The scripts, the container and bundle-ctl take their per-machine
values from `~/.config/odoo-dev/config.env`, described in
[`devcontainer/config.env.example`](devcontainer/config.env.example). `terminator-config` is mine
as it is: copy and adapt it rather than linking it.

## Setup on another machine

1. Clone this repo and the ones a bundle is made of under `REPO_ROOT` (default `~/repo`): `odoo`,
   `enterprise`, `design-themes`, `documentation`, `upgrade` and `upgrade-util` with the remotes
   `odoo` and `odoo-dev`, `owl` with `origin` and your fork (`OWL_DEV_REMOTE`), and `sfu`. The
   container also mounts [TestWarden](https://github.com/tsm-odoo/TestWarden),
   [DiscussModelParser](https://github.com/tsm-odoo/DiscussModelParser) and `~/.local/bin/gh`.
2. Write `~/.config/odoo-dev/config.env` for whatever differs from the defaults, at least
   `BUNDLE_SUFFIX` if your login is not your GitHub handle. `python3 devcontainer/config.py` shows
   what each key resolves to.
3. Put your git `[user]` in `~/.gitconfig.local`, which `.gitconfig` includes and the container
   mounts. Build the venv at `VENV`, give your postgres role no superuser (see
   [`devcontainer/README.md`](devcontainer/README.md)), and make the folders every bundle links to:
   `mkdir -p <WORKTREE_ROOT>/.claude <WORKTREE_ROOT>/.vscode`.
4. `python3 devcontainer/build.py --target odoo-dev`, until it warns about no missing mount source.
5. Install [`bundle-ctl`](bundle-ctl/) and the [`proxy`](proxy/) units, and source `.bashrc` from
   yours. What concerns only your machine goes in `~/.config/odoo-dev/bashrc.d/*.sh`, which it
   sources last.
