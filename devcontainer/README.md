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

## What a container cannot reach

The container runs as the host user (`--userns=keep-id`), so a file it can write is a file the host
trusts. What the host runs or loads as config, and a container has no reason to change, is
read-only: the shell helpers (`container-rw/`), the venv and the VS Code user settings.

Each container gets its own Claude config folder (`CLAUDE_CONFIG_DIR`, made by
[`claude-config.py`](claude-config.py) before every start): a `settings.json` copied from the host's
without its `model`, writable so a model can be picked in a tab, its own `session-env` and
`shell-snapshots`, and links to the rest of `~/.claude`. A model picked in a container lasts
until the container stops, and agent runs ask for the default model anyway.

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
  push;
- all of `~/.claude`, settings, hooks, skills and its `.git` included, and the bundles' shared
  `.claude`: they get fixed and committed from containers too, while the host's Claude runs them.
- the bundles' shared `.vscode`, as its workspace settings are changed from the container windows.
