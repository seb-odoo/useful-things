Collection of scripts that are useful in day-to-day.
Slow operations are batched and/or threaded.
Console tracks progress in real time.
All scripts are re-entrant.

Not fully re-usable yet, as the config has to be changed in-place, and not everything is configurable.

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
- Standard remote: fetches only sticky bundles (master, saas-*, 18.0, 17.0, 16.0)
- Deletes remote refs outside those two sets

```bash
$ python scripts/fetch_all.py
Repositories
├── design-themes
│   ├── [0.00-0.01] /home/seb/repo/design-themes ✅️ git branch -r
│   ├── odoo
│   │   └── [0.02-1.46] /home/seb/repo/design-themes ✅️ git fetch odoo master saas-19.2 saas-19.1 19.0 saas-18.4 saas-18.3 saas-18.2 18.0 17.0 16.0 -p
│   └── odoo-dev
│       └── [0.03-0.06] /home/seb/repo/design-themes ✅️ git branch --format=%(refname:short)
├── documentation
│   ├── [0.00-0.01] /home/seb/repo/documentation ✅️ git branch -r
│   ├── odoo
│   │   └── [0.03-1.95] /home/seb/repo/documentation ✅️ git fetch odoo master saas-19.2 saas-19.1 19.0 saas-18.4 saas-18.3 saas-18.2 18.0 17.0 16.0 -p
│   └── odoo-dev
│       └── [0.04-0.09] /home/seb/repo/documentation ✅️ git branch --format=%(refname:short)
├── enterprise
│   ├── [0.00-0.02] /home/seb/repo/enterprise ✅️ git branch -r
│   ├── odoo
│   │   └── [0.04-1.62] /home/seb/repo/enterprise ✅️ git fetch odoo master saas-19.2 saas-19.1 19.0 saas-18.4 saas-18.3 saas-18.2 18.0 17.0 16.0 -p
│   └── odoo-dev
│       ├── [0.06-0.10] /home/seb/repo/enterprise ✅️ git branch --format=%(refname:short)
│       └── [0.11-1.55] /home/seb/repo/enterprise ✅️ git fetch odoo-dev master-add_members-user--seb master-setupclass--seb master-member-user--seb -p
├── odoo
│   ├── [0.01-0.16] /home/seb/repo/odoo ✅️ git branch -r
│   ├── odoo
│   │   └── [0.16-2.66] /home/seb/repo/odoo ✅️ git fetch odoo master saas-19.2 saas-19.1 19.0 saas-18.4 saas-18.3 saas-18.2 18.0 17.0 16.0 -p
│   └── odoo-dev
│       ├── [0.17-0.36] /home/seb/repo/odoo ✅️ git branch --format=%(refname:short)
│       └── [0.37-2.26] /home/seb/repo/odoo ✅️ git fetch odoo-dev master-add_members-user--seb master-unique-chat--seb master-setupclass--seb master-member-user--seb master-livechat_to_store--seb master-split-channel-thread--seb 
│           master-transient-real-id--seb -p
├── upgrade-util
│   ├── [0.01-0.02] /home/seb/repo/upgrade-util ✅️ git branch -r
│   ├── odoo
│   │   └── [0.05-1.40] /home/seb/repo/upgrade-util ✅️ git fetch odoo master -p
│   └── odoo-dev
│       └── [0.08-0.09] /home/seb/repo/upgrade-util ✅️ git branch --format=%(refname:short)
└── upgrade
    ├── [0.02-0.12] /home/seb/repo/upgrade ✅️ git branch -r
    ├── odoo
    │   └── [0.12-2.21] /home/seb/repo/upgrade ✅️ git fetch odoo master -p
    └── odoo-dev
        ├── [0.13-0.25] /home/seb/repo/upgrade ✅️ git branch --format=%(refname:short)
        └── [0.26-1.87] /home/seb/repo/upgrade ✅️ git fetch odoo-dev master-unique-chat--seb -p
Done
```

## Support modules

- **config.py** — central configuration (repo paths, remotes, bundle suffix, sticky bundles); **edit this file to match your own setup**
- **commands.py** — pure helpers: name/path derivation for bundles, worktrees, and remotes
- **utils.py** — `Runner` subclass with Odoo-specific git operations (add/delete worktrees, fetch, branch switch)
- **command_runner.py** — subprocess execution infrastructure with live progress display and parallel runner
