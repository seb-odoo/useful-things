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
  - A bundle made before had `run` and `odoo` linked to the host's server and filestore, which
    reach every database. At its next start, [`bundle-db.py`](bundle-db.py) makes them folders of
    its own, with an empty `pg/16`, so it starts on an empty server of its own (Seb, 2026-10-28,
    security review M12). Its old databases stay on the host's server, which no bundle reaches.
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
test-warden engine comes back writable over it), the store of `node_modules`, whose trees every
bundle on the same `package.json` loads (`/opt/node_modules`, see "Shared node_modules" in
`scripts/README.md`), the browser of the Hoot runs, and what the next paragraphs name. The
`.agent` folder of the bundle is mounted on itself: bundle-ctl reads and writes its files by name,
and the container cannot put a link in the place of the folder.

**The browser of the Hoot runs.** The test-warden engine runs
`~/.cache/warden/chrome-headless-shell`, shared by every bundle and the host, and downloads it
there when it is missing. A bundle mounts that folder read-only, so
[`warden-chrome.py`](warden-chrome.py) downloads it on the host before the start of a bundle, once. A bundle started while that download failed (no
network) has no Hoot run until a later start. For a newer Chrome, delete the folder on the host.

**Claude.** A bundle has a Claude config folder of its own (`CLAUDE_CONFIG_DIR`,
`~/.cache/devcontainer/claude-config/<bundle>/home`), where what it shares of the host's
`~/.claude` is mounted name by name, as the table below says: a name nobody listed stays out of
the bundle. The host's Claude reads the shared files, so what a session takes as orders is
read-only there: a hook a bundle could write would run on the host.

Its login is a dummy one, made by [`claude-config.py`](claude-config.py) at each start and named
for the Claude switch of gateway-core, which sends its requests with the main login in its place
(gateway-core README, "The Claude switch"): no credential of the host is in a bundle, so code in a
bundle has no login to send away or swap. The bundle's Claude cannot renew it, and needs not: the
switch takes the access token of the host's login, which the ops sessions, and bundle-ctl while a
bundle runs, renew. What it costs (Seb, 2026-10-28): Remote Control and the claude.ai connectors of
the account, which the settings of a bundle do not load (`disableClaudeAiConnectors`). The ops
container keeps the host's `~/.claude` as its config folder, the login with it: never give a
container a copy or a link of `.credentials.json`, as a refresh replaces the link with a copy and
revokes every other one. `host-ctl/test_escape.py` holds the same list, and its `claude-entries`
line is OPEN on a name of the host's `~/.claude` that the list does not hold, as the ops container
writes it.

The plans have three folders, one per scope (Seb, 2026-10-28): the bundles plan in
`~/.local/share/claude-plans/dev` (a remote agent in its job, which gets none of the others), the
ops container in `~/.local/share/claude-plans/ops`, the host in `~/.claude/plans`, each mounted at
`~/.claude/plans` where it plans. A scope writes its own only: the ops container reads the plans
of the bundles as text at `~/dev-plans`, and a bundle sees no other. Claude's `plansDirectory`
cannot do it: it takes a folder of the project.

**Outside PRs.** A bundle on a PR of an author outside the odoo org reads the memory of the bundles
from a copy of its own, and what its agent writes there comes to Seb as a handoff card (Seb,
2026-10-29: "Yes, outside authors only"): a note a stranger's text steered reaches no other bundle
before his yes. [`scripts/outside.py`](../scripts/outside.py) marks such a bundle with the file
`outside` of `claude-config/<bundle>`, which no container mounts:

- at a `bctl fetch` of the PR (its author, as `gh api repos/R/pulls/N` reads it with the token of
  the host), or of the runbot label of a branch of a fork;
- at each start of the container, when an open PR of an author outside the org has the branch of
  the bundle as head, in a repo the bundle has (one GraphQL query; GitHub out of reach leaves the
  mark as it was).

Outside means a GitHub association to the repo other than `MEMBER` or `OWNER`, and a head on
neither `odoo` nor `odoo-dev`: the forward-ports of fw-bot are inside. A mark stays once set, also when the
bundle is deleted and made again under its name. A container that runs when its bundle gets marked
keeps the shared memory until it stops: a mount is fixed for the life of a container.

At its start, the memory of a marked bundle is `claude-config/<bundle>/memory`, the others mount the
shared one there, through a link. Its copy gets the notes of the shared memory, but the ones it
changed. bundle-ctl looks at the copy every minute: notes changed since the bundle got them, a
minute after the last change, go to `~/.claude/handoff/<bundle>@memory/dev-memory/`, merged into
what the shared memory got since (`git merge-file --union`), and hok shows them as one proposal;
newer notes wait for its answer. A yes writes them into the shared memory (host-ctl `handoff.py`,
root `dev-memory`), a no drops them from the copy at its next start. A note the bundle deletes is
not proposed. `memory-base` beside the copy holds what the bundle got last, read by the host only.
A remote agent takes the copy too, and its notes come back to it.

**Fake keys.** Each bundle has a GitHub token and an SSH key that look real and are not (Seb,
2026-10-29: "Yes, one per bundle"): `~/.config/gh/hosts.yml`, `GITHUB_PAT` in the environment of
its Claude, `~/.ssh/id_ed25519`. None of its tools sends them: gh sends the `GH_TOKEN` of the
GitHub gateway, git goes through git-gate. So a request that carries one comes from code that read
the keys of the bundle and tried them. [`claude-config.py`](claude-config.py) makes them once, in
`claude-config/<bundle>/fake-keys.json`, writes them again at each start, and names them in
`STATE_ROOT/gateway-callers/fake/<bundle>.json`, which every gateway mounts: a gateway refuses a
request that carries one (in its target, a header or its body, as sent, URL-encoded or in a JSON
string) and holds one card in hok for the bundle, and git-gate holds a push that carries one
(gateway-core README, "Fake keys"; git-gate README). Nothing shows before. What none of them sees:
a key sent out through the Claude tunnels, which no gateway reads, then used from elsewhere, where
GitHub only says it is no token; a key encoded otherwise (compressed, in base64 again), or in a
binary file of a push.

| name in `~/.claude` | in a bundle | in the ops container |
| --- | --- | --- |
| `.credentials.json` | its own: a dummy login | shared, writable: the login |
| `CLAUDE.md`, `agents`, `bin`, `commands`, `external`, `hooks`, `output-styles`, `plugins`, `routines`, `rules` (read as the user's own instructions), `skills`, `workflows` | shared, read-only: what a session takes as orders or runs | its draft |
| `launch.json`, `loop.md`, `scheduled_tasks.json`, `settings.local.json` | its own | shared, read-only: what Claude reads as the project folder of the home |
| `.git` | shared, read-only, by the fragment of host-ctl: the host commits there | same |
| `review-corpus` | shared, read-only: the reviews the review skill learns from | shared, writable |
| `projects` | its own, but `-workspace` (the transcripts and the memory of the bundles) shared and writable, the memory a copy of its own on an outside PR (above), and the memory of the host sessions read-only | shared, writable |
| `.claude.json`, `daemon.json`, `policy-limits.json`, `remote-settings-helper-consent`, `remote-settings.json`, `settings.json` | its own copy, below | same |
| `backups`, `cache`, `file-history`, `ide`, `session-env`, `sessions`, `shell-snapshots` | its own folder, below | same |
| `dev-mods` | its own folder, empty and read-only | its own folder |
| `agent-start.lock` | shared, writable: one lock of agent starts for every container | same |
| `handoff` | shared, read-only, also at `~/handoff`, where `handoff/<bundle>` is writable: a bundle proposes a change in its own folder only, which `hok` writes on a yes (host-ctl `handoff.py`) | shared, writable |
| `plans` | the plans of the dev agents, shared by the bundles | the plans of the ops container |
| `history.jsonl`, `paste-cache`, `debug`, `telemetry`, `stats-cache.json`, `.ruff_cache` | its own | shared, writable: prompts, logs and counts, which no session runs |
| `.last-cleanup`, `.last-config-audit`, `.last-update-result.json`, `policy-limits.json.stamp.json`, `remote-settings.json.synced-at`, and the `.signature.json` and `.signature-iat.json` of `policy-limits.json` and `remote-settings.json` | its own | shared, writable: dates and signatures. A copy of its own would need one more bind of the host on itself (below) |
| `.gitignore`, `README.md` | its own | shared, writable: files of the config repo, which the host commits by name |
| `.claude.json.tmp.*` | its own | shared, writable: what a save of `.claude.json` leaves when its rename fails |

What a tab has to write is a file of the bundle, made by [`claude-config.py`](claude-config.py)
before every start:

- `.claude.json`, the state of Claude, where an MCP server is kept: a copy of the host's with the
  workspace trusted, as one file for every container would carry a server added in one into the
  others. Written in place, since it is bound into the running container.
- the other files Claude writes in its config folder, each a copy of the bundle: the managed
  settings and their cache (`remote-settings.json`, `remote-settings-helper-consent`, which can
  carry hooks), `policy-limits.json`, `daemon.json`.
- for the ops container, read-only, what Claude reads in `~/.claude` as the project folder of the
  home, for a session started there: `scheduled_tasks.json` (prompts it runs by itself),
  `settings.local.json`, `launch.json`, `loop.md`. `claude-config.py` makes them on the host when
  missing, with nothing in them.
- `settings.json`, a copy of the host's, where a model picked in a tab is kept, and which turns off
  the claude.ai connectors in a bundle. Claude saves it by rename, which fails on a mounted file,
  and then writes it in place. The copy has no `ask` rule:
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
- what Claude keeps for itself and reads again in a later session, as for `.claude.json`:
  `backups`, `cache` (the list of models among them), `file-history` (what a rewind writes back
  in the files of a session) and `ide` (the lock files of the editor windows).
- `dev-mods`, empty and read-only in a bundle: Claude runs a mod of `dev-mods/<session>` in the
  Claude process of that session, and a session of the host would load one a bundle wrote there.
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
bundle folder. Open a bundle with `ocode`, a gbs link or `bctl`, which all carry that path. The
`initializeCommand` is an array, [`initialize.py`](initialize.py) with the folder name and the
folder as its arguments, so that no shell reads the name: a PR author chooses it, and `pfb`
refuses one that is no bundle name (`test_initialize.py`).

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
container. `remote.forwardOnOpen` does not cover that path, and the URL of any page can carry data
out of the network of the container. So VS Code opens links with
[`host-browser.py`](host-browser.py) (`workbench.externalBrowser`), which opens nothing itself: it
hands each page to firefox-gateway, cloned beside this repo, which checks it against the hosts of
its config (a localhost address on plain http port 80 only, where the Odoo proxy listens) and
holds it for a yes in `hok`, as the pages a container asks for. VS Code runs it for a link of any
of its windows with the URL alone, so the card cannot say which window asked, and the page opens
in no tab group. A page refused, or with no firefox-gateway, is a notification that says why.

**Postgres** cannot tell a container from the host (same uid on the same socket), so the role must
not be a superuser, or `COPY ... TO PROGRAM` runs commands on the host:

```bash
psql -d postgres -c "ALTER ROLE $USER NOSUPERUSER NOCREATEROLE"   # keeps CREATEDB
```

What stays open, and what closes it:

- the shared `.git` of every repo, as git has to work in a container. Its `config` and `hooks` are
  read-only there (security review of 2026-10-29, S1): a `core.fsmonitor`, an alias or a hook
  written there would run in the git of the host, of the ops container and of every other bundle.
  The cost, which Seb accepted: `git push -u`, `git remote add`, `git branch -u` and `git branch -m`
  fail in a container, a branch made there from a remote branch gets no upstream (two errors, the
  branch is made), and a change of the config on the host reaches a container at its next start
  (the bind holds the file of its start). So [`initialize.py`](initialize.py) has
  `scripts/upstream.py` set the upstream of the bundle branch on the dev remote before each start.
  `worktrees/` is read-only too but for the folder of the bundle's own worktree: there, the
  `commondir` file of another bundle's worktree points its git at a config of the writer's choice
  (proved on a copy, 10-29). git names that folder after the worktree folder (`odoo`, `odoo1`,
  ...), and the config every bundle shares can only name it after the bundle, so
  [`worktree-admin.py`](worktree-admin.py) moves it to `worktrees/<bundle>` and points the `.git`
  file of the worktree there: at each start, after `add_worktree`, before a remote agent. A repo
  the bundle has no worktree of gets an empty locked folder there, the source of its mount. The
  cost: no `git worktree add` in a bundle (Seb, 10-29: another bundle, or another agent, instead),
  and a `git gc` in a bundle prints two errors, the reflogs of the other worktrees. A running
  container keeps its mounts until it restarts. The rest stays writable: refs and objects, which
  every bundle shares.
  What the host runs from a bundle folder, like the `enable.sh` of a fetched branch, runs bundle
  code too. A machine closes both with `HOST_BOX` in `config.env`: a command that runs its arguments
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
