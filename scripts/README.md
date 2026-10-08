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

Bundles are named `<base>-<name><suffix>`, where the suffix is `BUNDLE_SUFFIX` (default `--$USER`).
Worktrees land in `WORKTREE_ROOT/<base>/<bundle_name>/<repo>/`. Both come from
`~/.config/odoo-dev/config.env`, see
[`../devcontainer/config.env.example`](../devcontainer/config.env.example); the examples below are
from a machine where they are `--seb` and `/home/seb/src/odoo`.

A PR fetched by `fetch_bundle.py` keeps its branch name. When that name starts with another version
than the PR target (or with none), its folder sits under the target, which is then its base.

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

Also takes a PR link (`fetch_bundle.py https://github.com/odoo/odoo/pull/292267`), or the `owner:branch` label runbot shows for a fork PR: the bundle is the PR head. When the head is on a fork, the fork is added as a remote named after its owner and the branch tracks it, so pull and push go to the PR. Deleting the last bundle tracking that remote removes it.

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

- Dev remote: fetches all locally checked-out branches, except those tracking a fork, fetched from that fork
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

### branch_status.py (`gbs`)

Lists the local branches of every repo like `git branch`, newest first: what is not pushed, how far
behind their base they are, whether a rebase conflicts, and their PR with its mergebot, review and
CI state. Reads local refs only, so run `gfa` first for fresh numbers.

Each branch name is an `odoo-bundle://` link, see `bundle_open.py`.

A bundle that would be in Waits on me goes to Waits on agents while a bundle-ctl agent works on it
or its task is queued, until the agent ends on a verdict. An agent whose window closed before its
verdict shows `agent stopped`: nothing runs it any more.

A pushed branch without PR shows `create PR`, a link to the GitHub compare page that opens one. A
branch with no commit on its base and no PR shows `no commit` and does not count for the group of
its bundle: it is the worktree's checkout of the base in a repo the bundle does not change.

`--json` prints the same data for a script: per bundle its group, issue, open state and agent, per
repo its head, push, behind, conflict and PR. A dev container cannot run it (no `scripts/`, no
podman): there `gbs` is `bctl branches`, and `bctl --json branches` gives the JSON, both run on
the host.

#### Groups

Each bundle sits in one group, the most urgent among its repos. On screen, in this order:

| Group | A bundle is there when |
| --- | --- |
| Open in VS Code | its dev container runs, so a window is open on it |
| Waits on me | no PR, a branch that is not pushed, a conflict, a failing check other than security, review threads someone else wrote last, changes requested, a staging error, a delegation with no r+ |
| Drafts | a draft of mine with nothing to do and no CI running |
| Waits on a reviewer | the PR is ready and nobody answered yet |
| Waits on CI | checks are running, or an r+ waits for them |
| Waits on agents | it would wait on me, but a bundle-ctl agent is being built, queued, running or between two turns |
| Waits on mergebot | ready or staged, merged or closed |
| Not mine | the branch of a colleague: no `BUNDLE_SUFFIX`, the fw-bot heads of my PRs count as mine |

When the repos of a bundle disagree, the bundle takes the first of: me, CI, drafts, reviewer,
mergebot, not mine.

Inside a group, a bundle delegated to me that still waits for my r+ comes first. Then:

- Waits on me goes by closeness to a merge: review feedback only, then red (conflict, failing
  check, staging error), then work in progress (no PR, not pushed, failing style).
- Waits on a reviewer goes by the colour of its ask tag, red first, the oldest wait first.
- Every other group shows the oldest first.

The ask tag of Waits on a reviewer says who asked, and how long ago:

| Tag | Meaning | Colour |
| --- | --- | --- |
| `not asked since push 2h` | the last ask is older than the last push, so it no longer counts | yellow |
| `auto-requested 3d` | only a bot or the opening of the PR asked for a review | dim under a day, yellow from a day, red from a week |
| `asked 3d` | a comment or a review request of mine | dim under 2 days, yellow from 2 days, red from a week |

#### Columns

Age, link, branch, repo with the PR as `repo/number`, push, behind, conflict, state.

- Age is dim past a week. The branch is cyan when a worktree has it checked out.
- Push is `+N` / `-N` against `odoo-dev/<branch>`, or `no remote`.
- Behind counts the commits of `odoo/<base>` the branch lacks: yellow from 150, red above 500.
- Conflict says whether a rebase on the base would conflict (`git merge-tree`).
- A merged PR leaves push, behind and conflict empty: the local copy is stale by then. A closed
  one keeps them, the local work may still matter.

One meaning per colour, in every column: red is act now, yellow is act soon, green is nothing
left to do, dim is background, cyan is a worktree, magenta is merged.

#### State

- The agent tag comes first: `building`, `queued N`, `running`, `waiting`, and a yellow
  `agent stopped` in Waits on me.
- CI counts as running while the main check of the repo is not posted on the head commit
  (`ci/runbot`, or `ci/runbot (light)` on a draft, `ci/documentation`, `ci/design-theme`,
  `ci/sfu`). Right after a push only the quick checks are there, all green, and the PR would read
  as done.
- A failure posted by an older runbot batch than the newest one on the commit is dropped while a
  check is still pending. `codeowner` is never shown, `security` is dim as it gets overridden,
  `style` is yellow.
- The mergebot state is read from `mergebot.odoo.com/<owner>/<repo>/pull/<n>.json`: green for
  `ready` and `staged`, a green `r+` for approved, red for an error. It also tells a PR the bot
  merged from one that was closed, which GitHub shows the same way.
- `delegated`, in green, is a review or a comment holding `@robodoo delegate+`.
- Review threads count apart: a yellow `N threads` when someone else wrote last, a dim
  `N replied` when I did.
- The state of a colleague's PR is dim as a whole.

All the PRs come from one GraphQL query, one alias per repo and branch, sent while git works. A
`gh` call that fails or a mergebot that does not answer prints `PR state incomplete, <source>:
<reason>` under the table, and `create PR` only shows when the PR query worked.

### bundle_open.py

Opens an `odoo-bundle://<bundle>` link: in its dev container when the worktree exists, like
`ocode`, otherwise through `pfb` in a terminal, which opens the dev container at the end.

One-time setup, so that a click on a `gbs` branch name reaches it:

```bash
$ ln -s ~/repo/useful-things/scripts/odoo-bundle.desktop ~/.local/share/applications/
$ xdg-mime default odoo-bundle.desktop x-scheme-handler/odoo-bundle
```

and add `odoo-bundle` to `terminal.integrated.allowedLinkSchemes` in the VS Code settings, next to
its default values.

## Support modules

- **agents.py**: the bundle-ctl queue and the `.agent/` files of each bundle, read by the daemon and `branch_status.py`
- **config.py** — central configuration (repo paths, remotes, bundle suffix, sticky bundles); **edit this file to match your own setup**
- **commands.py** — pure helpers: name/path derivation for bundles, worktrees, and remotes
- **utils.py** — `Runner` subclass with Odoo-specific git operations (add/delete worktrees, fetch, branch switch)
- **command_runner.py** — subprocess execution infrastructure with live progress display and parallel runner
