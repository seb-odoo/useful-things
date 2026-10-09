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
trusts. The rule: the host never runs, and never loads as config, a file a bundle container can
write. So these are read-only in a bundle: the shell helpers (`container-rw/`), the venv, the VS
Code user settings, TestWarden and DiscussModelParser (the cache of the test-warden engine comes
back writable over it), and what the next paragraphs name.

**Claude.** A container uses the host's `~/.claude` as its config folder (`CLAUDE_CONFIG_DIR`), so
it shares the host's login: a token refresh is locked and saved in that folder. Never give a
container a copy or a link of `.credentials.json`: a refresh replaces the link with a copy and
revokes every other one. The root of the folder stays writable for that, and what a session takes
as orders or runs comes back read-only over it: `CLAUDE.md`, `hooks`, `skills`, `bin`, `agents`,
`commands`, `output-styles`, `plugins`, `external`, and the memory of the host sessions. The host's
Claude reads the same files, so a hook a bundle could write would run on the host.
What a tab has to write is a file of the bundle, made by [`claude-config.py`](claude-config.py)
before every start:

- `settings.json`, a copy of the host's, where a model picked in a tab is kept. Claude saves it by
  rename, which fails on a mounted file, and then writes it in place. The copy has no `ask` rule:
  such a rule prompts even when a hook allowed the call, and in a container the hooks decide.
- the project `.claude`, a folder of the bundle where the files all bundles share (`CLAUDE.md`,
  `settings.json`) are links into a read-only mount, and `settings.local.json` is its own: an
  "always allow" holds for that bundle.
- `session-env`, `shell-snapshots`, and the storage of the Claude extension (the permission mode
  of each session, kept over a rebuild).

A bundle agent that has a fix for one of the read-only files hands it over instead of writing it.

**VS Code extensions.** Each container mounts a store of its own, read-only: code in an extension
runs in a window, and a window can type in a terminal of the host. The stores are hard-link clones
of a template only the host writes (`~/.cache/devcontainer/vscode-extensions-template`), made by
[`extensions.py`](extensions.py) before every start, and a bundle is cloned again when its
container is down and the template moved. A window cannot install or update an extension: the
installer of the VS Code server runs on the template, in a throwaway container that mounts nothing
else.

```bash
python3 devcontainer/extensions.py --add <publisher.name or file.vsix>   # in the template
python3 devcontainer/extensions.py --update                             # the newest of each
python3 devcontainer/extensions.py --remove <publisher.name>            # drops it from every store
```

[`extensions-update.timer`](extensions-update.timer) runs the update once a day. The bundled
claude-autoopen is added the same way after a change of its `.vsix`.

**The container config.** The tools name it at `WORKTREE_ROOT/.devcontainer/devcontainer.json`
(`get_devcontainer_config` in `scripts/commands.py`), never through the bundle folder: its
`initializeCommand` and its mounts apply on the host at the next start, and a container writes its
bundle folder. Open a bundle with `ocode`, a gbs link or `bctl`, which all carry that path.

**GitHub.** A container has no key. Its git goes through the connection the host keeps open, by
the mounts of the [`ssh-github-mux`](../ssh-github-mux/README.md) fragment at the root of this
repo, where the socket folder is read-only (host git goes through any socket there). Whoever holds
that socket pushes to any repo the account writes: a fragment of another repo can take its place
(`"replaces"`, see `build.py`) to put a policy between a container and GitHub.

VS Code relays the SSH agent of the environment it was started from (`SSH_AUTH_SOCK`) into every
container it attaches to, as `/tmp/vscode-ssh-auth-*.sock`, and has no setting against it. So the
agent of the session holds no key: each host of `~/.ssh/config` names its own with
`IdentityAgent`. `ssh-add -l` in a container window must list nothing.

**Postgres** cannot tell a container from the host (same uid on the same socket), so the role must
not be a superuser, or `COPY ... TO PROGRAM` runs commands on the host:

```bash
psql -d postgres -c "ALTER ROLE $USER NOSUPERUSER NOCREATEROLE"   # keeps CREATEDB
```

What stays open, and what closes it:

- the shared `.git` of every repo, config and hooks included, as git has to work in a container
  (`push -u`, upstreams): a `core.fsmonitor`, an alias or a hook written there runs in the git of
  the host. So does what the host runs from a bundle folder, like the `enable.sh` of a fetched
  branch. A machine closes both with `HOST_BOX` in `config.env`: a command that runs its arguments
  with the bundle repos and nothing else of the machine. The bundle scripts run `enable.sh` through
  it, with no network, and the git of the host can be a wrapper that does the same for these repos;
- the filestore and the databases, which the bundles share;
- the gpg agent and the git credential helper of the host, which VS Code relays into the
  containers it attaches to like the SSH agent. A secret gpg key of the host signs from a
  container, behind its passphrase: mask `gpg-agent-extra.socket` on the host, the socket that is
  relayed. The helper relay has no switch (`gitCredentialHelperConfigLocation` only stops writing
  the git config): a helper of the host answers `git credential fill` there, so the host has none;
- the bundles' shared `.vscode`, as its workspace settings are changed from the container windows;
- a VS Code window trusts the container it is attached to: with the stores read-only no code of
  ours or of a bundle gets into an extension, the rest is VS Code's own remote protocol.
