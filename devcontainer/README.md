# The dev container, generated

`devcontainer.json` is generated rather than committed, because a static file cannot say "only if
this machine has that": a mount whose source is missing breaks the container before it starts, and
an `--add-host` pointing at nothing breaks every tool that name covers.

```bash
python3 build.py --target odoo-dev            # writes WORKTREE_ROOT/.devcontainer/devcontainer.json
python3 build.py --target odoo-dev --check    # says whether it would change, writes nothing
python3 config.py                             # what the placeholders expand to here
```

- [`base.jsonc`](base.jsonc) is the container itself, minus whatever is optional. Edit this one.
- A repo cloned under `REPO_ROOT` contributes a `devcontainer.fragment.json`, naming per target the
  mounts, runArgs and env it needs. Arrays append in repo-name order; a `containerEnv` key ending
  in `+` appends to the comma-separated value already there, so two fragments can add to one
  variable.
- Nothing cloned means nothing merged: the container is the plain base, and it still runs.
- `@KEY@` placeholders come from [`config.py`](config.py), reading the `config.env` described in
  [`config.env.example`](config.env.example). No absolute path belongs in a committed file.

The generated file carries no `//` notes: they live here and in each fragment, which is what a human
reads. Every bundle symlinks `.devcontainer` to `WORKTREE_ROOT/.devcontainer`, so generating once
covers all of them; a container has to be rebuilt to pick up a change.

VS Code runs podman through [`vscode-podman.sh`](vscode-podman.sh) (`dev.containers.dockerPath`).
After `podman run`, Dev Containers waits for the start event of the container on a `podman events`
started at the same moment. When that `podman events` subscribes after the start, the window stays
on "Starting Dev Container" forever, so the wrapper adds `--since` to replay the event.

## How it runs Odoo

The container reuses the venv built on the host (`VENV`, mounted read-only): nothing is installed
with pip inside. The rest follows from that choice.

- **The base stays Ubuntu 22.04.** The `python-ldap` of the venv is linked against OpenLDAP 2.5,
  which Jammy ships. A newer base has 2.6 and breaks it.
- **System packages go in the [`Dockerfile`](../Dockerfile), at build time**: Python 3.12, the LDAP
  and SASL libraries, the postgres client, fonts, wkhtmltopdf, Chrome from Google's own package
  (Jammy's `chromium` is a snap stub that does not run in a container) and Node 22 (Jammy's is too
  old for Odoo's JS tooling). The container runs with `no-new-privileges` as a non-root user, so
  nothing can be installed once it is up.
- **The venv is first in `PATH` for every process**, through `containerEnv`, with the standard
  directories written out: `${containerEnv:PATH}` is not expanded there and would replace the whole
  `PATH`. The `python3` of the image is 3.10, and `odoo-bin` or a test started outside an
  interactive shell would pick it.
- **The database is the host's**, over its unix socket: `/var/run/postgresql` is mounted, with
  `PGHOST` and `PGUSER` set. `--userns=keep-id` gives the container the uid of the host user, so
  peer authentication sees the same role as on the host. The host's postgres only listens on
  localhost, so TCP from the container would be refused.
- **Browser tests run inside.** Odoo starts Chrome with `--no-sandbox --disable-dev-shm-usage`,
  which is enough under `--cap-drop=ALL` and the small `/dev/shm`.
- **Commits are not signed in a container.** The `.git/config` shared with the host asks for a
  signature with a key the container does not have. `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_0` and
  `GIT_CONFIG_VALUE_0` in `containerEnv` turn it off there only: the environment wins over every
  config file, and the host keeps signing.
- **The shell helpers reach non-interactive shells** through `BASH_ENV`, which points at
  `container-rw/devcontainer.bashrc`: an agent runs `bash -c`, which does not read `~/.bashrc`.

A change to `containerEnv` needs a rebuild: "Reopen in Container" reuses the container that exists.
A container left half-made by a failed build blocks the next start with "container state
improper": remove it.

`scripts/utils.py` opens a bundle straight in its container, with
`code --folder-uri vscode-remote://dev-container+<hex>/workspace`, where `<hex>` is the hex of the
JSON VS Code itself writes for that container: the host path, the podman socket, the config file.
If a VS Code release changes that format, read the new one in its storage and match it.

## What a container cannot reach

The container runs as the host user (`--userns=keep-id`), so a file it can write is a file the host
trusts. What the host runs or loads as config, and a container has no reason to change, is
read-only: the shell helpers (`container-rw/`), the venv, the VS Code user settings and the socket
folder of [`ssh-github-mux`](../ssh-github-mux/README.md) (host git goes through any socket there).

Claude in a container uses the host's `~/.claude` as its config folder (`CLAUDE_CONFIG_DIR`), so
it shares the host's login: a token refresh is locked and saved in that folder. Never give a
container a copy or a link of `.credentials.json`: a refresh replaces the link with a copy and
revokes every other one. Each container gets its own `session-env`, `shell-snapshots` and storage
of the Claude extension (the permission mode of each session, kept over a rebuild), made by
[`claude-config.py`](claude-config.py) before every start.

Each container also gets its own VS Code extension store, made by
[`extensions.py`](extensions.py) before every start. A VS Code server rewrites `extensions.json` in
place and locks nothing between processes, so two servers on one store corrupt it, and every window
opened after that loads no extension. The stores are hard-link clones of a template only the host
writes (`~/.cache/devcontainer/vscode-extensions-template`): what a server installed goes to the
template at its bundle's next start, and reaches another bundle at the next start that finds its
container down. The files are the same inodes in every store, so a write in place to an extension's
file in one container reaches the others. An uninstall in one bundle is not followed:

```bash
python3 devcontainer/extensions.py --remove <publisher.name>   # drops it from every store
```

Postgres cannot tell a container from the host (same uid on the same socket), so the role must not
be a superuser, or `COPY ... TO PROGRAM` runs commands on the host:

```bash
psql -d postgres -c "ALTER ROLE $USER NOSUPERUSER NOCREATEROLE"   # keeps CREATEDB
```

What stays open on purpose:

- the bundle folder itself: anything the host runs inside a bundle (its git hooks, npm, odoo-bin)
  runs code the container can change;
- the shared `.git` of every repo, config and hooks included, as git has to work in a container
  (`push -u`, upstreams): a `core.fsmonitor`, an alias or a hook written there runs in the host's git;
- TestWarden and DiscussModelParser, which get fixed and committed from containers too, while the
  host runs them;
- the filestore and the databases;
- the SSH agent VS Code forwards into the containers it attaches to, which lets an agent fetch and
  push (the container of an agent in a terminal has none, and reaches github.com through
  ssh-github-mux alone);
- all of `~/.claude`, settings, hooks, skills and its `.git` included, and the bundles' shared
  `.claude`: they get fixed and committed from containers too, while the host's Claude runs them.
- the bundles' shared `.vscode`, as its workspace settings are changed from the container windows.
