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
  mounts, runArgs and env it needs. Arrays append in repo-name order, and an array key ending in
  `-` (`"runArgs-"`) takes its items out of the array, so a fragment can put its own argument in
  the place of one of the base. A `containerEnv` key ending in `+` appends to the comma-separated
  value already there, so two fragments can add to one variable.
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
  and SASL libraries, the postgres client and the server of the bundle (below), fonts,
  wkhtmltopdf 0.12.6.1 with patched qt from wkhtmltopdf/packaging, the one of Odoo's own image
  (Jammy's has no header nor footer, and the PDF report tests fail with it), Chrome from Google's
  own package (Jammy's `chromium` is a snap stub that does not run in a container) and Node 22
  (Jammy's is too old for Odoo's JS tooling). The container runs with `no-new-privileges` as a
  non-root user, so nothing can be installed once it is up.
- **The venv is first in `PATH` for every process**, through `containerEnv`, with the standard
  directories written out: `${containerEnv:PATH}` is not expanded there and would replace the whole
  `PATH`. The `python3` of the image is 3.10, and `odoo-bin` or a test started outside an
  interactive shell would pick it.
- **Each bundle has its own postgres server**, in its container, since 2026-10-19: a bundle can
  neither read nor drop the databases of another, and its databases go with it (Seb's choice).
  The image carries PGDG's postgres 16 with pgvector, and a ready cluster in `/opt/bundle-pg/16`
  made at build time, `template_vector` included. `SHARE_ROOT/bundle-db/<bundle>` holds `pg` (the
  cluster, in `pg/16`), `run` (its socket, mounted at `/var/run/postgresql`, so `PGHOST` stays)
  and `odoo` (the filestore, at `~/.local/share/Odoo`). The creation of the bundle makes them
  before any worktree, so a bare start of its agent has them too (`test_optional_repos.py`), and
  `delete_bundle` removes them.
  - [`container-rw/bundle-pg.sh`](../container-rw/bundle-pg.sh) copies the cluster of the image
    at the first start, and starts the server at each one: from `postStartCommand`, and from
    `agent-run.sh`, as `devcontainer up --skip-post-create` skips it. When the server runs
    already, as in an agent terminal of an open window, `bundle-pg.sh` returns at once
    (`test_bundle_pg.py`). The server runs as `vscode`,
    so the host user owns its files. A stop of the container kills it, and the next start replays
    its WAL: `fsync` stays on, and `synchronous_commit = off` loses the last commits at most.
  - The role is `PGUSER`, superuser of its own server with `trust` on the socket, so Odoo creates
    the `vector` extension itself.
  - Sized for test databases (measured 2026-10-16): 22 MB of memory when idle, about 80 MB during
    an install of `mail`, about 33 MB of disk beside the databases, thanks to `wal_recycle = off`
    and WAL segments of 1 MB. `max_parallel_workers_per_gather = 0`, as the `/dev/shm` of a
    container is 63 MB.
  - A bundle made before keeps the host's server and filestore. At its next start,
    [`bundle-db.py`](bundle-db.py) finds no folder and makes `run` and `odoo` links to them, with
    an empty `pg`, so `bundle-pg.sh` starts nothing. Over that socket, `--userns=keep-id` gives
    the container the uid of the host user, so peer authentication sees the same role as on the
    host.
- **Browser tests run inside.** Odoo starts Chrome with `--no-sandbox --disable-dev-shm-usage`,
  which is enough under `--cap-drop=ALL` and the small `/dev/shm`.
- **Commits are not signed in a container.** The `.git/config` shared with the host asks for a
  signature with a key the container does not have. `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_0` and
  `GIT_CONFIG_VALUE_0` in `containerEnv` turn it off there only: the environment wins over every
  config file, and the host keeps signing.
- **The shell helpers reach non-interactive shells** through `BASH_ENV`, which points at
  `container-rw/devcontainer.bashrc`: an agent runs `bash -c`, which does not read `~/.bashrc`.
  Claude Code then sources a copy of the functions it took at the start of the session, which
  wins: a function changed in the bashrc reaches a running agent only through a nested `bash -c`.
- **Claude runs from the extension.** At its first interactive start, the CLI copies itself into
  `~/.local/share/claude` (250 MB in the layer of each container, 12 GB for 45 of them on
  2026-10-13), and nothing runs that copy. `DISABLE_AUTOUPDATER` stops it.

A container keeps the mounts and the environment it was made with: a start takes the one that
exists again, whatever the config says since. So `ocode`, `bctl open` and the launch of an agent
first remove the stopped containers of the bundle made before the shared config was last written,
or from another config file (`drop_stale_containers` in `scripts/commands.py`, cases in
`scripts/test_containers.py`): closing a window and opening the bundle again is enough to apply a
config change. A running container is left alone, and the open says it runs on an older config;
nothing in a container can replace it. The ops container stays up when its window closes, as the
ops agent windows run in it: the open gives the `podman rm -f` that replaces it. `build.py` does
not rewrite an unchanged config, as its date is what tells. A container left half-made by a
failed build blocks the next start with "container state improper": remove it.

`scripts/utils.py` opens a bundle straight in its container, with
`code --folder-uri vscode-remote://dev-container+<hex>/workspace`, where `<hex>` is the hex of the
JSON VS Code itself writes for that container: the host path, the podman socket, the config file.
If a VS Code release changes that format, read the new one in its storage and match it.

## What a container cannot reach

The container runs as the host user (`--userns=keep-id`), so a file it can write is a file the host
trusts. The rule: the host never runs, and never loads as config, a file a bundle container can
write. So these are read-only in a bundle: the shell helpers (`container-rw/`), the venv, the VS
Code user settings, TestWarden, DiscussModelParser and runbot-fix-agents (the cache of the
test-warden engine comes back writable over it), and what the next paragraphs name.

**Claude.** A container uses the host's `~/.claude` as its config folder (`CLAUDE_CONFIG_DIR`), so
it shares the host's login: a token refresh is locked and saved in that folder. Never give a
container a copy or a link of `.credentials.json`: a refresh replaces the link with a copy and
revokes every other one. The root of the folder stays writable for that, and what a session takes
as orders or runs comes back read-only over it: `CLAUDE.md`, `hooks`, `skills`, `bin`, `agents`,
`commands`, `output-styles`, `plugins`, `external`, `rules` (read as the user's own instructions),
`routines`, `workflows`, and the memory of the host sessions. A name Claude Code adds there later
is writable until it joins this list: `host-ctl/test_escape.py` names the ones it checks. The host's
Claude reads the same files, so a hook a bundle could write would run on the host.
What a tab has to write is a file of the bundle, made by [`claude-config.py`](claude-config.py)
before every start:

- `.claude.json`, the state of Claude, where an MCP server is kept: a copy of the host's with the
  workspace trusted, as one file for every container would carry a server added in one into the
  others. Written in place, since it is bound into the running container.
- the other files Claude writes in its config folder, each a copy of the bundle: the managed
  settings and their cache (`remote-settings.json`, `remote-settings-helper-consent`, which can
  carry hooks), `policy-limits.json`, `daemon.json`.
- read-only, what Claude reads in `~/.claude` as the project folder of the home, for a session
  started there: `scheduled_tasks.json` (prompts it runs by itself), `settings.local.json`,
  `launch.json`, `loop.md`. `claude-config.py` makes them on the host when missing, with nothing
  in them.
- `settings.json`, a copy of the host's, where a model picked in a tab is kept. Claude saves it by
  rename, which fails on a mounted file, and then writes it in place. The copy has no `ask` rule:
  such a rule prompts even when a hook allowed the call, and in a container the hooks decide.
  It also excludes the host path of `~/.claude/CLAUDE.md`: a container that mounts `~/.claude`
  at that path too (the ops container) would load the file a second time, as the project file of
  an ancestor of its workspace, 11 KB in every session and every subagent.
  The host binds its own `settings.json`, `CLAUDE.md` and every other file a container mounts
  over on themselves, once, with `sudo devcontainer/settings-bind.sh`. Without that bind a write
  of the file on the host is a rename (a save of Claude, an Edit of a session, a `git checkout`),
  and the rename drops what every running container mounts over it: each then reads and writes
  the host's file. A container whose copy is gone renames the host's file in turn and drops the
  copy of the others (`.claude.json`, 2026-10-12: every running container shared it). A
  container that lost its copy can drop the bind of the host the same way, so restart it. On the
  host, git cannot replace these files any more (`Device or resource busy`): a `checkout` leaves
  the old content, a `pull` or a `reset --hard` stops with the other files rewritten. Write the
  file in place first, `git show REV:settings.json > settings.json`. A read-only mount of the
  whole `~/.claude` binds each of these files again read-only: a bound file comes into it as a
  mount of its own, which stays writable, and podman cannot start a container with
  `bind-nonrecursive` on that folder (`Invalid argument`).
- the project `.claude`, a folder of the bundle where the files all bundles share (`CLAUDE.md`,
  `settings.json`) are links into a read-only mount, and `settings.local.json` is its own: an
  "always allow" holds for that bundle.
- `session-env`, `shell-snapshots`, and the storage of the Claude extension (the permission mode
  of each session, kept over a rebuild).
- `sessions`, where Claude keeps the record of each live session, named after its pid: the claude
  of every container has the same few pids (21 or 22 for an agent in a terminal), so in the
  shared folder the records overwrite each other, and a claude that ends deletes the record of
  another container's.

A bundle agent that has a fix for one of the read-only files hands it over instead of writing it,
in `~/handoff/<bundle>/` (host-ctl README, "Handoff"). That folder is the host's
`~/.claude/handoff`, mounted again out of `~/.claude`: Claude asks before a write under a
`.claude` folder (its "protected paths"), even when a hook allowed it, and in auto mode a `cp` there
reached Seb as a prompt (2026-10-21).

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

To look into a container and its store:

- `podman exec -u vscode <container> ...`: as root (cap-drop ALL) `~/.vscode-server` is
  Permission denied. Server logs: `~/.vscode-server/data/logs/<UTC start>/remoteagent.log`, times
  in UTC. A terminal agent container has no log folder, it runs no VS Code server.
- Which store a container mounts: `podman inspect <c> --format '{{range .Mounts}}{{if eq
  .Destination "/home/vscode/.vscode-server/extensions"}}{{.Source}}{{end}}{{end}}'`.
- Does VS Code read a store: `code --extensions-dir <store> --list-extensions --show-versions` on
  the host (the same scanner as the server, opens no window). In a container with no window:
  `/vscode/vscode-server/bin/linux-x64/*/bin/code-server --extensions-dir
  ~/.vscode-server/extensions --list-extensions --show-versions`.
- What a window activated: the `_doActivateExtension` lines of `exthost1/remoteexthost.log` in
  the server logs (`Anthropic.claude-code` has a capital A there, grep -i).
- The server watches `extensions.json` (`Extensions added from another source ...`), so a change
  made from outside is picked up live. `extensions.json` is a real file per store, every other
  file is the same inode in all stores. `delete_bundle` removes the bundle's store.

Host user settings apply to dev container windows too, so a setting like
`git.blame.editorDecoration.enabled` only needs to be set once in the host's
`~/.config/Code/User/settings.json`. Per-extension state (`data/User/globalStorage`) is per
container and lost on rebuild, except Claude's (a per-bundle mount).

**The container config.** The tools name it at `WORKTREE_ROOT/.devcontainer/devcontainer.json`
(`get_devcontainer_config` in `scripts/commands.py`), never through the bundle folder: its
`initializeCommand` and its mounts apply on the host at the next start, and a container writes its
bundle folder. Open a bundle with `ocode`, a gbs link or `bctl`, which all carry that path.

**The network.** The base gives a container the default bridge (`--net=bridge`): the whole
internet, and every network the host routes, a VPN included. What the container reads can then be
sent anywhere. A fragment closes it by putting the container on an internal network of its own in
the place of the bridge (`"runArgs-": ["--net=bridge"]`), with a proxy as the one way out.

**GitHub.** A container has no key. Its git goes through the connection the host keeps open, by
the mounts of the [`ssh-github-mux`](../ssh-github-mux/README.md) fragment at the root of this
repo, where the socket folder is read-only (host git goes through any socket there). Whoever holds
that socket pushes to any repo the account writes: a fragment of another repo can take its place
(`"replaces"`, see `build.py`) to put a policy between a container and GitHub.

VS Code relays the SSH agent of the environment it was started from (`SSH_AUTH_SOCK`) into every
container it attaches to, as `/tmp/vscode-ssh-auth-*.sock`, and has no setting against it. So the
agent of the session holds no key: each host of `~/.ssh/config` names its own with
`IdentityAgent`. `ssh-add -l` in a container window must list nothing.

It also opens a link a container asks for (`$BROWSER`, `code --openExternal`) in the browser of the
host, and for a localhost address it first forwards the port into the container, with no prompt:
a page the container serves then runs its scripts in the host browser, outside the network of the
container. `remote.forwardOnOpen` does not cover that path. So VS Code opens links with
[`host-browser.py`](host-browser.py) (`workbench.externalBrowser`), which refuses a localhost
address on any port but plain http on 80, where the Odoo proxy listens, and hands the rest to the
default browser.

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
