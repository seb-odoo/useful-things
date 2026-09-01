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
