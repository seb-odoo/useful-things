Collection of scripts that are useful in day-to-day.

## Bundle management

Bundles are named `<base>-<name><suffix>` where suffix is set in `config.py` (`BUNDLE_SUFFIX`, e.g. `--seb`).
Worktrees land in `/home/seb/src/odoo/<bundle_name>/<repo>/`.

### create_bundle.py

Creates a new bundle locally for a given base branch across all configured repos.

```bash
python scripts/create_bundle.py <base> <name>
# e.g. python scripts/create_bundle.py master my-feature
```

Fetches the base branch, creates worktrees, and (if run from inside a repo dir) pushes a local branch to the dev remote.

### delete_bundle.py

Removes a bundle from the local system (worktrees, branches, filestore, databases).

```bash
python scripts/delete_bundle.py <bundle_name> [--also-remote]
# e.g. python scripts/delete_bundle.py master-my-feature--seb
```

- Deletes git worktrees and local branches for each repo
- Drops matching PostgreSQL databases
- Removes filestore entries under `~/.local/share/Odoo/filestore/`
- `--also-remote`: also deletes the remote dev branch. ⚠️ No not use on branches of others!

### fetch_bundle.py

Fetches bundle metadata from runbot and creates/updates local worktrees.

```bash
python scripts/fetch_bundle.py <bundle_name>
# e.g. python scripts/fetch_bundle.py master-my-feature--seb
```

Queries the runbot API, checks out each repo at the matching commit, links shared `node_modules`, runs web tooling setup, and opens the bundle in VS Code.

### fetch_all.py

Syncs git remotes: fetches locally checked-out branches + sticky bundles; prunes everything else.

```bash
python scripts/fetch_all.py
```

- Dev remote: fetches all locally checked-out branches
- Standard remote: fetches only sticky bundles (master, saas-*, 18.0, 17.0, 16.0)
- Deletes remote refs outside those two sets

## Support modules

- **config.py** — central configuration (repo paths, remotes, bundle suffix, sticky bundles); **edit this file to match your own setup**
- **commands.py** — pure helpers: name/path derivation for bundles, worktrees, and remotes
- **utils.py** — `Runner` subclass with Odoo-specific git operations (add/delete worktrees, fetch, branch switch)
- **command_runner.py** — subprocess execution infrastructure with live progress display and parallel runner
