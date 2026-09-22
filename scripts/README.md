Collection of scripts that are useful in day-to-day.
Slow operations are batched and/or threaded.
Console tracks progress in real time.
All scripts are re-entrant.

Not fully re-usable yet, as the config has to be changed in-place, and not everything is configurable.

## Shell completion

`autocomplete/enable.sh`/`disable.sh` let you quickly turn tab completion on or off: they add or
remove a marked block in `~/.bashrc` that sets up `create_bundle`, `delete_bundle` and
`fetch_bundle` as aliases and wires up completion for `create_bundle`/`delete_bundle`.

## Bundle management

Bundles are named `<base>-<name><suffix>` where suffix is set in `config.py` (`BUNDLE_SUFFIX`, e.g. `--seb`).
Worktrees land in `/home/seb/src/odoo/<base>/<bundle_name>/<repo>/`.

### create_bundle.py

Creates a new bundle locally for a given base branch across all configured repos.
Fetches the base branch, creates worktrees, and (if run from inside a repo dir) pushes a local branch to the dev remote.
Links shared `node_modules`, runs web tooling setup, and opens the bundle in VS Code.

```bash
$ python scripts/create_bundle.py master test
[0.00-0.01] current folder ✅️ mkdir -p /home/seb/src/odoo/master/master-test--seb
[0.01-0.01] current folder ✅️ ln -sfn /home/seb/repo/useful-things/odools.toml /home/seb/src/odoo/master/master-test--seb/odools.toml
[0.01-0.01] current folder ✅️ ln -sfn /home/seb/src/odoo/.vscode /home/seb/src/odoo/master/master-test--seb/.vscode
Repositories
├── design-themes
│   ├── [0.01-1.33] /home/seb/repo/design-themes ✅️ git fetch odoo master -p
│   └── [1.33-1.49] /home/seb/repo/design-themes ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/design-themes odoo/master
├── documentation
│   ├── [0.01-1.69] /home/seb/repo/documentation ✅️ git fetch odoo master -p
│   └── [1.70-3.32] /home/seb/repo/documentation ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/documentation odoo/master
├── enterprise
│   ├── [0.01-1.55] /home/seb/repo/enterprise ✅️ git fetch odoo master -p
│   └── [1.55-3.42] /home/seb/repo/enterprise ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/enterprise odoo/master
├── odoo
│   ├── [0.02-3.61] /home/seb/repo/odoo ✅️ git fetch odoo master -p
│   ├── [3.61-5.95] /home/seb/repo/odoo ✅️ git worktree add -B master-test--seb /home/seb/src/odoo/master/master-test--seb/odoo odoo/master
│   └── [5.96-12.05] /home/seb/src/odoo/master/master-test--seb/odoo ✅️ git push -u odoo-dev master-test--seb
├── upgrade-util
│   ├── [0.02-1.36] /home/seb/repo/upgrade-util ✅️ git fetch odoo master -p
│   └── [1.36-1.38] /home/seb/repo/upgrade-util ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/upgrade-util odoo/master
└── upgrade
    ├── [0.02-2.38] /home/seb/repo/upgrade ✅️ git fetch odoo master -p
    └── [2.39-3.28] /home/seb/repo/upgrade ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/upgrade odoo/master
[12.05-12.05] /home/seb/src/odoo/master/master-test--seb ✅️ mkdir -p /home/seb/src/odoo/master/odoo/node_modules
[12.05-12.06] /home/seb/src/odoo/master/master-test--seb ✅️ ln -sfn /home/seb/src/odoo/master/odoo/node_modules /home/seb/src/odoo/master/master-test--seb/odoo/node_modules
[12.06-12.06] /home/seb/src/odoo/master/master-test--seb ✅️ mkdir -p /home/seb/src/odoo/master/enterprise/node_modules
[12.06-12.06] /home/seb/src/odoo/master/master-test--seb ✅️ ln -sfn /home/seb/src/odoo/master/enterprise/node_modules /home/seb/src/odoo/master/master-test--seb/enterprise/node_modules
[12.06-19.58] /home/seb/src/odoo/master/master-test--seb ✅️ bash ./odoo/addons/web/tooling/enable.sh
y

[19.58-19.76] /home/seb/src/odoo/master/master-test--seb ✅️ code .
Done
```

### fetch_bundle.py

Fetches bundle metadata from runbot and creates/updates local worktrees.

Queries the runbot API, checks out each repo at the matching commit, links shared `node_modules`, runs web tooling setup, and opens the bundle in VS Code.

```bash
$ python scripts/fetch_bundle.py master-test--seb
Fetching https://runbot.odoo.com/api/bundle?name=master-test--seb
{
    'id': 451581,
    'name': 'master-test--seb',
    'commits': [
        {'repo': 'odoo', 'name': '293f65e02d0fce7c56fa097c9ee2d4357609c8a3'},
        {'repo': 'enterprise', 'name': '2fef66a9a74e6a248ea356d87337e6e1635cac85'},
        {'repo': 'design-themes', 'name': 'f820b108e739e482a525166ad13e57da10bebf42'},
        {'repo': 'upgrade', 'name': '25e433d7fc8653a22834f9b24b7bde44f76a5063'},
        {'repo': 'documentation', 'name': 'e825ada98833d382edab4686ae47ce933576b79d'},
        {'repo': 'upgrade-util', 'name': '8a61e891df29e0cc0fc7c4159f2139506471a8d8'}
    ],
    'unstable_commits': [
        {'repo': 'odoo', 'name': '293f65e02d0fce7c56fa097c9ee2d4357609c8a3'},
        {'repo': 'enterprise', 'name': '2fef66a9a74e6a248ea356d87337e6e1635cac85'},
        {'repo': 'design-themes', 'name': 'f820b108e739e482a525166ad13e57da10bebf42'},
        {'repo': 'upgrade', 'name': '25e433d7fc8653a22834f9b24b7bde44f76a5063'},
        {'repo': 'documentation', 'name': 'e825ada98833d382edab4686ae47ce933576b79d'},
        {'repo': 'upgrade-util', 'name': '8a61e891df29e0cc0fc7c4159f2139506471a8d8'}
    ],
    'branches': [{'remote': 'git@github.com:odoo-dev/odoo', 'repo': 'odoo', 'name': 'master-test--seb', 'is_pr': False, 'draft': False}],
    'last_batch': 2447576,
    'last_done_batch': 2447576,
    'null': None
}
[0.17-0.17] current folder ✅️ mkdir -p /home/seb/src/odoo/master/master-test--seb
[0.17-0.17] current folder ✅️ ln -sfn /home/seb/repo/useful-things/odools.toml /home/seb/src/odoo/master/master-test--seb/odools.toml
[0.17-0.17] current folder ✅️ ln -sfn /home/seb/src/odoo/.vscode /home/seb/src/odoo/master/master-test--seb/.vscode
Commits
├── odoo
│   ├── [0.17-2.51] /home/seb/repo/odoo ✅️ git fetch odoo-dev master-test--seb -p
│   └── [2.52-5.26] /home/seb/repo/odoo ✅️ git worktree add -B master-test--seb /home/seb/src/odoo/master/master-test--seb/odoo odoo-dev/master-test--seb --track
├── enterprise
│   ├── [0.18-1.73] /home/seb/repo/enterprise ✅️ git fetch odoo 2fef66a9a74e6a248ea356d87337e6e1635cac85 -p
│   └── [1.73-3.54] /home/seb/repo/enterprise ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/enterprise 2fef66a9a74e6a248ea356d87337e6e1635cac85
├── design-themes
│   ├── [0.18-1.66] /home/seb/repo/design-themes ✅️ git fetch odoo f820b108e739e482a525166ad13e57da10bebf42 -p
│   └── [1.66-1.96] /home/seb/repo/design-themes ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/design-themes f820b108e739e482a525166ad13e57da10bebf42
├── upgrade
│   ├── [0.18-1.89] /home/seb/repo/upgrade ✅️ git fetch odoo 25e433d7fc8653a22834f9b24b7bde44f76a5063 -p
│   └── [1.90-2.32] /home/seb/repo/upgrade ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/upgrade 25e433d7fc8653a22834f9b24b7bde44f76a5063
├── documentation
│   ├── [0.18-1.60] /home/seb/repo/documentation ✅️ git fetch odoo e825ada98833d382edab4686ae47ce933576b79d -p
│   └── [1.61-3.18] /home/seb/repo/documentation ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/documentation e825ada98833d382edab4686ae47ce933576b79d
└── upgrade-util
    ├── [0.19-1.59] /home/seb/repo/upgrade-util ✅️ git fetch odoo 8a61e891df29e0cc0fc7c4159f2139506471a8d8 -p
    └── [1.59-1.61] /home/seb/repo/upgrade-util ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/upgrade-util 8a61e891df29e0cc0fc7c4159f2139506471a8d8
[5.27-5.27] /home/seb/src/odoo/master/master-test--seb ✅️ mkdir -p /home/seb/src/odoo/master/odoo/node_modules
[5.27-5.28] /home/seb/src/odoo/master/master-test--seb ✅️ ln -sfn /home/seb/src/odoo/master/odoo/node_modules /home/seb/src/odoo/master/master-test--seb/odoo/node_modules
[5.28-5.28] /home/seb/src/odoo/master/master-test--seb ✅️ mkdir -p /home/seb/src/odoo/master/enterprise/node_modules
[5.28-5.28] /home/seb/src/odoo/master/master-test--seb ✅️ ln -sfn /home/seb/src/odoo/master/enterprise/node_modules /home/seb/src/odoo/master/master-test--seb/enterprise/node_modules
[5.28-8.29] /home/seb/src/odoo/master/master-test--seb ✅️ bash ./odoo/addons/web/tooling/enable.sh
y

[8.29-8.48] /home/seb/src/odoo/master/master-test--seb ✅️ code .
Done
```

### delete_bundle.py

Removes a bundle from the local system (worktrees, branches, filestore, databases).

- Deletes git worktrees and local branches for each repo
- Drops matching PostgreSQL databases
- Removes filestore entries under `~/.local/share/Odoo/filestore/`
- Completes the bundle names having a worktree folder (needs `argcomplete`'s global completion,
  see `activate-global-python-argcomplete`)
- Accepts several bundles at once, deleted in parallel (one thread per bundle)
- Accepts `fnmatch` patterns, expanded against the existing worktree folders: `'saas-19.*'`
- Leaves a bundle untouched, and exits 1, when one of its worktrees holds uncommitted files or its
  branch holds commits no remote has: they are listed, and the other bundles of the run are still
  deleted
- `--force`: delete such a bundle anyway, unsaved work included
- `--also-remote`: also deletes the remote dev branch. ⚠️ Do not use on branches of others!

```bash
$ python scripts/delete_bundle.py master-test--seb
Repositories
├── design-themes
│   ├── [0.00-0.73] /home/seb/repo/design-themes ✅️ git worktree remove /home/seb/src/odoo/master/master-test--seb/design-themes
│   ├── [0.73-0.74] /home/seb/repo/design-themes ✅️ git update-ref -d refs/remotes/odoo/master-test--seb
│   ├── [0.74-0.75] /home/seb/repo/design-themes ✅️ git update-ref -d refs/remotes/odoo-dev/master-test--seb
│   └── [0.75-0.78] /home/seb/repo/design-themes ❎️ git branch -D master-test--seb
│       error: branch 'master-test--seb' not found
├── documentation
│   ├── [0.00-1.06] /home/seb/repo/documentation ✅️ git worktree remove /home/seb/src/odoo/master/master-test--seb/documentation
│   ├── [1.06-1.07] /home/seb/repo/documentation ✅️ git update-ref -d refs/remotes/odoo/master-test--seb
│   ├── [1.07-1.08] /home/seb/repo/documentation ✅️ git update-ref -d refs/remotes/odoo-dev/master-test--seb
│   └── [1.09-1.10] /home/seb/repo/documentation ❎️ git branch -D master-test--seb
│       error: branch 'master-test--seb' not found
├── enterprise
│   ├── [0.01-2.07] /home/seb/repo/enterprise ✅️ git worktree remove /home/seb/src/odoo/master/master-test--seb/enterprise
│   ├── [2.08-2.09] /home/seb/repo/enterprise ✅️ git update-ref -d refs/remotes/odoo/master-test--seb
│   ├── [2.10-2.11] /home/seb/repo/enterprise ✅️ git update-ref -d refs/remotes/odoo-dev/master-test--seb
│   └── [2.11-2.13] /home/seb/repo/enterprise ❎️ git branch -D master-test--seb
│       error: branch 'master-test--seb' not found
├── odoo
│   ├── [0.01-3.21] /home/seb/repo/odoo ✅️ git worktree remove /home/seb/src/odoo/master/master-test--seb/odoo
│   ├── [3.21-3.22] /home/seb/repo/odoo ✅️ git update-ref -d refs/remotes/odoo/master-test--seb
│   ├── [3.23-3.24] /home/seb/repo/odoo ✅️ git update-ref -d refs/remotes/odoo-dev/master-test--seb
│   └── [3.24-3.26] /home/seb/repo/odoo ✅️ git branch -D master-test--seb
├── upgrade-util
│   ├── [0.01-0.06] /home/seb/repo/upgrade-util ✅️ git worktree remove /home/seb/src/odoo/master/master-test--seb/upgrade-util
│   ├── [0.06-0.07] /home/seb/repo/upgrade-util ✅️ git update-ref -d refs/remotes/odoo/master-test--seb
│   ├── [0.08-0.08] /home/seb/repo/upgrade-util ✅️ git update-ref -d refs/remotes/odoo-dev/master-test--seb
│   └── [0.09-0.09] /home/seb/repo/upgrade-util ❎️ git branch -D master-test--seb
│       error: branch 'master-test--seb' not found
└── upgrade
    ├── [0.02-0.76] /home/seb/repo/upgrade ✅️ git worktree remove /home/seb/src/odoo/master/master-test--seb/upgrade
    ├── [0.77-0.78] /home/seb/repo/upgrade ✅️ git update-ref -d refs/remotes/odoo/master-test--seb
    ├── [0.78-0.79] /home/seb/repo/upgrade ✅️ git update-ref -d refs/remotes/odoo-dev/master-test--seb
    └── [0.79-0.80] /home/seb/repo/upgrade ❎️ git branch -D master-test--seb
        error: branch 'master-test--seb' not found
[3.27-3.28] current folder ✅️ rm -rf /home/seb/src/odoo/master/master-test--seb
Files
└── /home/seb/.local/share/Odoo/filestore/master-test--seb-e-t
    ├── [3.28-3.29] current folder ✅️ rm -rf /home/seb/.local/share/Odoo/filestore/master-test--seb-e-t
    └── [3.29-3.43] current folder ✅️ dropdb master-test--seb-e-t
Done
```

### fetch_all.py

Syncs git remotes: fetches locally checked-out branches + sticky bundles; prunes everything else.

- Dev remote: fetches all locally checked-out branches
- Standard remote: fetches only sticky bundles (`STICKY_BUNDLES` in `config.py`)
- Deletes the handled remote's own refs outside those two sets

```bash
$ python scripts/fetch_all.py
Repositories
├── design-themes
│   ├── [0.00-0.01] /home/seb/repo/design-themes ✅️ git branch -r
│   ├── [0.01-1.55] /home/seb/repo/design-themes ✅️ git fetch odoo master 20.0
│   │   saas-19.4 saas-19.3 saas-19.2 saas-19.1 19.0 saas-18.4 saas-18.3
│   │   saas-18.2 18.0 17.0 16.0 -p
│   └── [1.55-1.56] /home/seb/repo/design-themes ✅️ git for-each-ref
│       --format=%(refname:short) refs/heads/
├── documentation
│   ├── [0.00-0.01] /home/seb/repo/documentation ✅️ git branch -r
│   ├── [0.01-2.13] /home/seb/repo/documentation ✅️ git fetch odoo master 20.0
│   │   saas-19.4 saas-19.3 saas-19.2 saas-19.1 19.0 saas-18.4 saas-18.3
│   │   saas-18.2 18.0 17.0 16.0 -p
│   ├── [2.13-2.13] /home/seb/repo/documentation ✅️ git for-each-ref
│   │   --format=%(refname:short) refs/heads/
│   └── [2.13-3.79] /home/seb/repo/documentation ✅️ git fetch odoo-dev
│       master-store-doc--seb -p
├── enterprise
│   ├── [0.00-0.01] /home/seb/repo/enterprise ✅️ git branch -r
│   ├── [0.01-1.64] /home/seb/repo/enterprise ✅️ git fetch odoo master 20.0
│   │   saas-19.4 saas-19.3 saas-19.2 saas-19.1 19.0 saas-18.4 saas-18.3
│   │   saas-18.2 18.0 17.0 16.0 -p
│   ├── [1.64-1.64] /home/seb/repo/enterprise ✅️ git for-each-ref
│   │   --format=%(refname:short) refs/heads/
│   └── [1.64-3.13] /home/seb/repo/enterprise ✅️ git fetch odoo-dev
│       master-onrelationchange--seb master-recipient-uids--seb
│       master-record-signal--seb master-signal-props--seb
│       master-split-channel-thread--seb -p
├── odoo
│   ├── [0.00-0.02] /home/seb/repo/odoo ✅️ git branch -r
│   ├── [0.02-1.65] /home/seb/repo/odoo ✅️ git fetch odoo master 20.0 saas-19.4
│   │   saas-19.3 saas-19.2 saas-19.1 19.0 saas-18.4 saas-18.3 saas-18.2 18.0
│   │   17.0 16.0 -p
│   ├── [1.65-1.65] /home/seb/repo/odoo ✅️ git for-each-ref
│   │   --format=%(refname:short) refs/heads/
│   └── [1.65-3.15] /home/seb/repo/odoo ✅️ git fetch odoo-dev
│       19.0-convert-inline-946970--seb
│       20.0-light-user-notification-preferences-disabled-aku
│       master-lazy-field-map--seb master-record-signal--seb
│       master-split-channel-thread--seb -p
├── owl
│   ├── [0.00-0.01] /home/seb/repo/owl ✅️ git branch -r
│   ├── [0.01-0.62] /home/seb/repo/owl ✅️ git fetch origin master -p
│   ├── [0.62-0.62] /home/seb/repo/owl ✅️ git for-each-ref
│   │   --format=%(refname:short) refs/heads/
│   └── [0.62-2.02] /home/seb/repo/owl ✅️ git fetch seb-odoo
│       master-signal-props--seb master-string-types--seb -p
├── sfu
│   ├── [0.00-0.01] /home/seb/repo/sfu ✅️ git branch -r
│   ├── [0.01-0.01] /home/seb/repo/sfu ✅️ git for-each-ref
│   │   --format=%(refname:short) refs/heads/
│   └── [0.01-1.50] /home/seb/repo/sfu ✅️ git fetch origin master
│       master-recording2-tso -p
├── upgrade-util
│   ├── [0.00-0.01] /home/seb/repo/upgrade-util ✅️ git branch -r
│   ├── [0.01-1.58] /home/seb/repo/upgrade-util ✅️ git fetch odoo master -p
│   └── [1.58-1.58] /home/seb/repo/upgrade-util ✅️ git for-each-ref
│       --format=%(refname:short) refs/heads/
└── upgrade
    ├── [0.00-0.01] /home/seb/repo/upgrade ✅️ git branch -r
    ├── [0.01-2.11] /home/seb/repo/upgrade ✅️ git fetch odoo master -p
    ├── [2.11-2.11] /home/seb/repo/upgrade ✅️ git for-each-ref
    │   --format=%(refname:short) refs/heads/
    └── [2.11-3.54] /home/seb/repo/upgrade ✅️ git fetch odoo-dev
        master-split-channel-thread--seb -p
Done
```

## Support modules

- **config.py** — central configuration (repo paths, remotes, bundle suffix, sticky bundles); **edit this file to match your own setup**
- **commands.py** — pure helpers: name/path derivation for bundles, worktrees, and remotes
- **utils.py** — `Runner` subclass with Odoo-specific git operations (add/delete worktrees, fetch, branch switch)
- **command_runner.py** — subprocess execution infrastructure with live progress display and parallel runner
