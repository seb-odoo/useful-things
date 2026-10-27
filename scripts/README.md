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

### The repos of a bundle

A bundle gets a worktree of each repo of `folder_by_repo` in `config.py` (`design-themes` and
`documentation` only when they have the branch). owl and sfu differ from the Odoo repos:

- owl's dev remote is a fork, `OWL_DEV_REMOTE` in `config.env`. Its worktrees start with no
  `node_modules` on purpose. The clone's own install predates the monorepo (npm workspaces,
  vitest), so sharing it the way odoo and enterprise share theirs would seed a tree that does
  not match `package-lock.json`. A build in a worktree pays for its own `npm ci`, and it writes
  `packages/owl/dist/owl.iife.js`, which is what odoo vendors as
  `addons/web/static/lib/owl/owl.js`.
- sfu has one remote for both roles. `remote_by_repo["sfu"]` and `remote_dev_by_repo["sfu"]` are
  both `origin` (`odoo/sfu`): the team pushes dev branches to the upstream repo itself, and there
  is no fork. sfu is the only repo shaped this way, which is why `fetch_all.handle_repo`
  deduplicates its remote loop with `dict.fromkeys`; without it the same namespace is fetched and
  pruned twice.
- A bundle named after a colleague's sfu branch is his branch: `pfb <his branch>` puts the
  worktree on it, tracking `origin`, so a commit pushed from it lands on his open PR. owl cannot
  do this, its dev remote being a personal fork. `delete_bundle --also-remote` likewise deletes
  that branch on `odoo/sfu`, and that stays unguarded on purpose: the flag is only for when the
  remote branch should go, so a check on who owns it would only be in the way.
- No `node_modules` for sfu either: `_install_js_tooling` covers only odoo and enterprise.
  mediasoup builds or downloads a native worker binary, and `npm ci` in a worktree leaves its
  postinstall blocked by npm's allow-scripts policy, so the worker needs
  `npm approve-scripts mediasoup` before the server will start. Tests run without it
  (`tests/rtc.test.ts` asserts a CPU-count-dependent `NUM_WORKERS` and fails on some machines,
  upstream, not a local breakage).

### Shared node_modules

The bundles share a store of `node_modules`, one tree per `package.json` of the web tooling
(`WORKTREE_ROOT/.node_modules/<key>/`, with that `package.json` and its `package-lock.json` beside
the tree), which only the host writes. The key is the start of the sha256 of the file
(`node_modules_key` in `devcontainer/config.py`), so the bases with the same file share one tree:
on 2026-10-27, 17.0 to saas-18.4 had one, and 20.0, saas-19.4 and master before its ESLint 10 had
another. Each container mounts the store read-only at `/opt/node_modules`, and the `node_modules`
of its `odoo/` and `enterprise/` is a link to `/opt/node_modules/<key>/node_modules`, the key of
its own `odoo/addons/web/tooling/_package.json`: a bundle not rebased yet keeps the tree of its
branch when its base moves to another one (2026-10-26, master went to ESLint 10, whose config the
ESLint 8 of the master bundles not rebased cannot read). `_install_js_tooling` makes the link after
`enable.sh`, and `devcontainer/initialize.py` makes it again before each start: a container that
started bare got its worktrees from `bctl build` after its start, so only the first one links them.
A folder of their own there goes at that start: the hard links of the base a bundle had before
2026-10-21 let it write the files that the ESLint of every other bundle of the base loads.

A rebase onto a branch with another `package.json` changes the key in a container that runs. Odoo's
pre-commit hook then reloads the tooling: `disable.sh` deletes the link and `enable.sh` runs
`npm install`, which reaches no registry from a bundle (403). The `npm` function of
`container-rw/devcontainer.bashrc` (`BASH_ENV`, so the hook's bash has it) takes that
`npm install` in an odoo worktree and makes the link to the new key instead; `enable.sh` copies it
to enterprise with `cp -a`. When the store has no tree for the key, the link waits for it and the
commit fails with a line that names `bctl node-modules`.

`_install_js_tooling` in `utils.py` runs `enable.sh` for its config files and its hooks, with npm in
dry run: `npm install` would replace the link with a tree of its own, built from the branch. npm
is also offline: in the box, with no network, it retried the registry for 14 minutes before it gave
up (2026-10-21), and offline it gives up at once; `enable.sh` goes on after npm fails. Do not
patch `enable.sh` instead: it is tracked upstream (`odoo/addons/web/tooling/enable.sh`), so a hand
patch is gone in the next worktree. The fix of a tooling file of Odoo belongs in these scripts.

`bctl node-modules` builds the tree of the `package.json` of the web tooling of a base's branch,
when the store has none: npm installs that file in a box with the network (`HOST_BOX`), in a folder
of its own that then goes in the store under its key. A tree never changes once there, and a
bundle linked to its key loads it at once, even in a container that runs. A bundle asks it for its
own base and waits for Seb's yes, which `hok` shows with the change from the `package.json` of the
tree it links; the host asks it at once. The daemon also asks it itself, every minute, for each
base whose odoo head moved since it last looked and whose `package.json` has no tree: the bundles
fetch the bases in the shared `.git`, so the tree is there before their first commit on it (Seb,
2026-10-27). Nothing deletes a tree yet: one that no bundle links can go by hand.

Before 2026-10-27 each base had its own tree, `WORKTREE_ROOT/<base>/node_modules` (and
`.node_modules.old`). A container started before then still mounts it, and it can go once none
runs.

A bundle can arrive with only some of its repos. A runbot batch still in preparation lists a
commit only for the repos pushed so far, so `fetch_bundle` falls back to each missing repo's base
branch, and the node_modules step skips a repo that has no worktree at all.

### create_bundle.py

Creates a new bundle locally for a given base branch across all configured repos, but
`design-themes` and `documentation` (`OPTIONAL_REPOS` in `config.py`) only when they get the branch
(`--branch-repo`): 0.7 GB less per bundle. `add_repo.py` adds one later.
Fetches the base branch, creates worktrees, and (if run from inside a repo dir) pushes a local branch to the dev remote.
The base is never the upstream of the new branch. With `--no-push` the branch has no upstream until
a plain `git push`, which creates it on the dev remote and tracks it there
(`branch.<name>.pushRemote`, and `push.autoSetupRemote` in the config of the repo;
`test_upstream.py`).
Runs web tooling setup, writes `.made`, and opens the bundle in VS Code. `.made` comes last: a
bundle cut before it (a restart of bundle-ctl kills its jobs) is half-made, and the same command
again finishes it.
The setup script (`enable.sh`) comes from the branch: with `HOST_BOX` in `config.env` it runs boxed,
with no network.

```bash
$ python scripts/create_bundle.py master test
[0.00-0.01] current folder ✅️ mkdir -p /home/seb/src/odoo/master/master-test--seb
[0.01-0.01] current folder ✅️ ln -sfn /home/seb/repo/useful-things/odools.toml /home/seb/src/odoo/master/master-test--seb/odools.toml
[0.01-0.01] current folder ✅️ ln -sfn /home/seb/src/odoo/.vscode /home/seb/src/odoo/master/master-test--seb/.vscode
Repositories
├── enterprise
│   ├── [0.01-1.55] /home/seb/repo/enterprise ✅️ git fetch odoo master -p
│   └── [1.55-3.42] /home/seb/repo/enterprise ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/enterprise odoo/master
├── odoo
│   ├── [0.02-3.61] /home/seb/repo/odoo ✅️ git fetch odoo master -p
│   ├── [3.61-5.95] /home/seb/repo/odoo ✅️ git worktree add -B master-test--seb --no-track /home/seb/src/odoo/master/master-test--seb/odoo odoo/master
│   └── [5.96-12.05] /home/seb/src/odoo/master/master-test--seb/odoo ✅️ git push -u odoo-dev master-test--seb
├── upgrade-util
│   ├── [0.02-1.36] /home/seb/repo/upgrade-util ✅️ git fetch odoo master -p
│   └── [1.36-1.38] /home/seb/repo/upgrade-util ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/upgrade-util odoo/master
└── upgrade
    ├── [0.02-2.38] /home/seb/repo/upgrade ✅️ git fetch odoo master -p
    └── [2.39-3.28] /home/seb/repo/upgrade ✅️ git worktree add /home/seb/src/odoo/master/master-test--seb/upgrade odoo/master
[12.78-19.58] /home/seb/src/odoo/master/master-test--seb ✅️ bash ./odoo/addons/web/tooling/enable.sh
y
[19.58-19.58] /home/seb/src/odoo/master/master-test--seb ✅️ touch /home/seb/src/odoo/master/master-test--seb/.made
[19.61-19.76] /home/seb/src/odoo/master/master-test--seb ✅️ code --folder-uri vscode-remote://dev-container+<hex>/workspace
Done
```

### fetch_bundle.py

Fetches bundle metadata from runbot and creates/updates local worktrees.

Queries the runbot API, checks out each repo at the matching commit, runs web tooling setup, and opens the bundle in VS Code. `design-themes` and `documentation` get a worktree only when the bundle has a branch there.

`pfb -t BUNDLE` opens the terminator window of its agent instead, as `ocode -t` does, for a bundle
that is not local yet or was deleted: a deleted bundle lost its `.agent`, so its agent is a new one
with no prompt.

Also takes a PR link (`fetch_bundle.py https://github.com/odoo/odoo/pull/292267`), or the `owner:branch` label runbot shows for a fork PR: the bundle is the PR head. A head that is no bundle name (`BUNDLE_NAME` in `commands.py`: letters, digits, dots, dashes and underscores, 100 at most) is refused before anything is made, as the author of the PR chooses it and it goes unquoted into the mount options of the container and the config of the proxy (`test_bundle_names.py`). When the head is on a fork, the fork is added as a remote named after its owner and the branch tracks it, so pull and push go to the PR. Deleting the last bundle tracking that remote removes it.

Runbot names the bundle of a fork PR with the full label `<owner>:<head>`, while an odoo-dev
bundle has no prefix. Asked without the prefix, the API answers `"id": false` and pfb falls back
to the base tip for the other repos.

Runbot watches neither owl nor sfu, so a bundle's API answer never names them. For any repo runbot
said nothing about, it asks the dev remote for a branch of the bundle's name. A bundle named after
a base branch is excluded: the base clone has that branch checked out, so `worktree add -B` would
fail against it. A branch pushed only to the owl fork or to `origin/sfu` has no runbot bundle at
all: an answer with no id is read as an empty bundle, and the run only stops when neither runbot,
a dev remote nor a local branch has the name, or when runbot does not answer.

`pfb`, `ocode`, `delete_bundle`, `goto` and `opencode` take a bundle name under any dev remote
prefix, not just `odoo-dev:` (owl's is the remote of the fork, `OWL_DEV_REMOTE`): an unstripped
prefix in the runbot query made `get_base_from_bundle_name` read the fork owner as the base.

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
│   └── [2.52-5.26] /home/seb/repo/odoo ✅️ git worktree add -B master-test--seb --track /home/seb/src/odoo/master/master-test--seb/odoo odoo-dev/master-test--seb
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
[5.27-5.28] /home/seb/src/odoo/master/master-test--seb ✅️ rm -rf /home/seb/src/odoo/master/master-test--seb/odoo/node_modules
[5.28-5.29] /home/seb/src/odoo/master/master-test--seb ✅️ rm -rf /home/seb/src/odoo/master/master-test--seb/enterprise/node_modules
[5.29-5.62] /home/seb/src/odoo/master/master-test--seb ✅️ env npm_config_dry_run=true npm_config_offline=true npm_config_audit=false npm_config_fund=false bash ./odoo/addons/web/tooling/enable.sh
y

[5.62-5.63] /home/seb/src/odoo/master/master-test--seb ✅️ ln -sfn /opt/node_modules/d0ab58a5b38e43e2/node_modules /home/seb/src/odoo/master/master-test--seb/odoo/node_modules
[5.63-5.64] /home/seb/src/odoo/master/master-test--seb ✅️ ln -sfn /opt/node_modules/d0ab58a5b38e43e2/node_modules /home/seb/src/odoo/master/master-test--seb/enterprise/node_modules
[5.64-5.64] /home/seb/src/odoo/master/master-test--seb ✅️ touch /home/seb/src/odoo/master/master-test--seb/.made
[5.64-5.80] /home/seb/src/odoo/master/master-test--seb ✅️ code --folder-uri vscode-remote://dev-container+<hex>/workspace
Done
```

### add_repo.py

Adds to a bundle the worktree of a repo it was made without, `design-themes` or `documentation`: on
the bundle branch when the repo has one, here or on the dev remote, else on the base branch. The
folder shows at once in the container of the bundle, which mounts the bundle folder; the terminal
of the repo comes at the next window reload. From a container: `bctl add-repo documentation`.
`test_optional_repos.py` runs it, and `create_bundle.py`, on scratch repos.

```bash
$ python scripts/add_repo.py master-test--seb documentation
```

### repack_repos.py

Writes the objects of each base repo in one pack, once a week through
[`repos-repack.timer`](repos-repack.timer) (install steps in
[`repos-repack.service`](repos-repack.service)). git maintenance merges only the small packs, so
an object fetched twice stays in two big ones: on 2026-10-12 the 30 GB of packs of the odoo repo
held 15.5 million objects, 10.3 million of them distinct, and one repack brought them to 12.9 GB
in 28 minutes. A day later, its packs held 18.3 GB again. `git repack -a -d -k` keeps every
object, reachable or not, and prunes no worktree. A repo is skipped when the disk has less free
than its packs plus 10 GB, as the new pack is written before the old ones go.
`test_repack_repos.py` runs it on a scratch repo.

### delete_bundle.py

Removes a bundle from the local system (worktrees, branches, filestore, databases, images).

- Deletes git worktrees and local branches for each repo
- Removes `~/.local/share/bundle-db/<bundle>`: the postgres server and the filestore of the
  bundle (devcontainer/README.md, "How it runs Odoo")
- For a bundle made before its own server: drops the matching databases of the host's server and
  removes their filestore entries under `~/.local/share/Odoo/filestore/`
- Removes the podman images VS Code built for the bundle once its container is gone:
  `localhost/vsc-<bundle>-<sha256 of the bundle folder>`, and the same with `-uid`. The name is
  matched whole (`test_bundle_images.py`): as a prefix, `vsc-master-foo-` also takes the images of
  `master-foo-bar`. Before 2026-10-27 nothing removed them: 672 tags of deleted bundles held about
  24 GB, which `podman system df` did not show
- Completes the bundle names having a worktree folder (needs `argcomplete`'s global completion,
  see `activate-global-python-argcomplete`)
- Accepts several bundles at once, deleted in parallel (one thread per bundle)
- Accepts `fnmatch` patterns, expanded against the existing worktree folders: `'saas-19.*'`
- Leaves a bundle untouched, and exits 1, when one of its worktrees holds uncommitted files or its
  branch holds commits no remote has: they are listed, and the other bundles of the run are still
  deleted
- Leaves a bundle whose dev container still runs, with a yellow line: delete it again once its
  window is closed
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

- Dev remote: fetches the local branches pushed there at least once, except those tracking a fork, fetched from that fork
- Standard remote: fetches only sticky bundles (`STICKY_BUNDLES` in `config.py`), and sets them
  as its fetch list (`git remote set-branches`), so a plain `git fetch odoo` in a bundle takes
  them only. Taking every branch, it downloaded the history of the old ones again each time,
  since gfa deletes their refs: 1.58M objects, 5.3 GB and up to an hour for odoo (2026-10-19)
- Deletes the handled remote's own refs outside those two sets
- Fetches again when another fetch moved a ref meanwhile (`cannot lock ref ... but expected`):
  the bundle agents share the `.git` of each repo
- Asks before deleting a gone bundle of mine (`--seb`); `--keep-gone` keeps them all without
  asking, for a run with no keyboard (`hctl`) and for `gbs`
- Creates the local branch of each open PR of mine and of each forward-port waiting on me
- Deletes a never-pushed branch that the other repos of its bundle left out

A branch is gone when it has a ref of the dev remote (`refs/remotes/<dev>/<branch>`, which any
push writes, with or without `-u`) and the fetch no longer finds it there. Without the
`BUNDLE_SUFFIX` it goes through `delete_bundle(force=True)` with no prompt (worktree, branch and
uncommitted work, all lost); with the suffix it gets a "Gone ref ... Delete it? (Y/n)" prompt
that defaults to yes. A never-pushed branch (`create_bundle --no-push`, tracking `odoo/<base>`; a
hand-made `git fetch odoo pull/N/head:<x>`) is not fetched, so never gone. Limit: a branch whose
tracking ref was pruned by hand (`git fetch -p odoo-dev`) is skipped too, so it is never
deleted, never fetched, until the next push. The branch of a fork PR is gone the same way, so its
bundle goes once the contributor deletes the branch. So a bundle branch lives on the dev remote of
its repo: the fork of `OWL_DEV_REMOTE` for owl, `origin` itself for sfu.

Before fetching, one GitHub search lists the open PRs of mine and the forward-ports fw-bot opened
for me: `is:pr is:open author:fw-bot mentions:<login> repo:odoo/odoo repo:odoo/enterprise ...`.
`org:odoo` returns 0 results for fw-bot, the `repo:` qualifiers are needed. A forward-port is kept
when its head holds `<BUNDLE_SUFFIX>-` and ends with `-fw` (fw-bot pings reviewers too), and:

- label `conflict`, or
- head `statusCheckRollup.state` FAILURE/ERROR (pending is ignored), or
- a fw-bot comment that asks for an r+ (`FW_ASKS`: the last hop of the chain, the next hop in
  conflict or out of the chain, its own head moved, its cherry-pick conflict to resolve) without a
  later `@robodoo ... r+`, in a comment or a review. The ask itself writes `@robodoo r+`, so the
  search starts after it.

gfa then fetches it from the dev remote and runs `git branch --track` (no worktree; `pfb <head>`
for a bundle). Cleanup: gbs deletes a merged hop it sees locally, dev branch included. Otherwise
fw-bot deletes the dev branch 7 days after the merge, the fetch sees it gone, and a name without
the suffix goes through `delete_bundle(force=True)` with no prompt.

At the end, a bundle branch with no dev ref and commits over its base, while another repo of the
bundle has an open PR of mine, is deleted with no prompt: pushing the other repos alone means it
was left out. Skipped while the bundle's agent is queued, running or waiting, or its dev container
runs; 0 commits over base is left alone (the empty checkout of a repo the bundle does not change).
It goes through `delete_branch_and_remote_ref`, so the worktree stays detached on the commit, and
gfa prints `Dropped <repo>/<branch> at <sha>`: `git branch <branch> <sha>` brings it back.

To try a change of `handle_repo_remote`, exec `fetch_all.py` up to `runner.parallel_run(` with a
fake runner (real `for-each-ref`/`branch -r`, recorded `git_fetch`), never by running gfa itself:
it force-deletes.

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

#### A new release

`STICKY_BUNDLES` in `config.py` is the list of releases the tooling tracks, newest first. A new
release needs it there, plus a `grNN` alias in `container-rw/devcontainer.bashrc` (the host
sources that file too). Two things that bite:

- gfa fetches exactly this list from the upstream remote and deletes every upstream-tracking ref
  outside it. A release missing from the list is pruned at every run, so it looks absent locally
  while it exists upstream.
- A name in the list that no repo has on the remote is gone, and gfa then calls
  `delete_bundle(force=True)` on it with no prompt, dropping every database and filestore whose
  name starts with it. Check with `git ls-remote --heads odoo refs/heads/<version>` in odoo,
  enterprise, design-themes and documentation before adding it.

### branch_status.py (`gbs`)

Lists the local branches of every repo like `git branch`, newest first: what is not pushed, how far
behind their base they are, whether a rebase conflicts, and their PR with its mergebot, review and
CI state. Reads local refs only, so the host alias `gbs` runs `gfa --keep-gone` first: a gone
bundle stays, deleted next by gbs when its PRs are merged, listed when they are closed.

The icon left of each branch is an `odoo-bundle://terminal/` link, see `bundle_open.py`: it brings
up the VS Code window of the bundle when one is open, else the terminal of its agent (Seb,
2026-10-23: "gbs button should open in terminal (unless vs code is already open for that
agent)").

A bundle that would be in Waits on me goes to Waits on agents while a bundle-ctl agent works on it
or its task is queued, until the agent ends on a verdict. An agent whose window closed before its
verdict shows `agent stopped`: nothing runs it any more.

Opening a bundle (its icon, `ocode`, `ocode -t`) is handling it: from that click, a bundle
that would be in Waits on me goes to the group its PRs give without what waits on me (CI, drafts,
reviewer; drafts with no PR), and stays there until the task of a new agent is written, something
new waits on me, or a day passed since the click and since gbs last saw its window open
(`.agent/handled.open`): a bundle I opened and left goes back to Waits on me and to the automatic
triage (Seb, 2026-10-27, after odoo#296017 sat 8 days with its conflict). The click touches
`.agent/handled` and keeps in `.agent/handled.json` what waits on me then: each review thread to answer, each failing check of
a head, a conflict of a head, changes requested, a staging error of a head, a delegation. An item
missing from that list is something new, and puts the bundle back in Waits on me; an item that
goes away (a thread I answered) is not. `test_handled.py` runs each case.

A pushed branch without PR shows `create PR`, a link to the GitHub compare page that opens one. A
branch with no commit on its base and no PR shows `no commit` and does not count for the group of
its bundle: it is the worktree's checkout of the base in a repo the bundle does not change.

A bundle left with merged PRs and such checkouts only is deleted, worktrees and databases with it,
and named under the table: `Deleted <bundle>, merged: odoo#N`. Merged is what mergebot says, or
GitHub for a repo mergebot does not merge. The bundle stays, its PR in magenta, while its dev
container runs, an agent works on it, a worktree holds uncommitted files, or its branch holds a
commit the PR head does not. Its branch on the dev remote goes with it when the PR is mine (or a
forward-port of mine) and the branch is still at the PR head: fw-bot deletes it only 7 days after
the merge, and a branch pushed again since, for a new PR, stays. `test_merged_bundles.py` runs
each case on scratch repos.

`--json` prints the same data for a script: per bundle its group, issue, handled, open state and agent, per
repo its head, push, behind, conflict and PR, and under `deleted` the PRs of each bundle it
deleted. A dev container cannot run it (no `scripts/`, no
podman, and its `git` refuses the repos of `REPO_ROOT`, a mount point owned by root: dubious
ownership): there `gbs` is `bctl branches`, and `bctl --json branches` gives the JSON, both run on
the host. The daemon runs this script as a new process at each call, so a change here needs no
restart of the daemon (a change of `daemon.py` does).

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

A red check stops failing once a `@robodoo override=` of it, in a comment or a review posted after
the head commit, sets it aside for mergebot: GitHub still shows it red.

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

All the PRs come from GraphQL queries of 5 branches each, one alias per repo and branch, sent
together while git works: GitHub takes about 0.1 s per PR of a query, so one query for every
branch made the table wait 5 s or more. A `gh` call that fails or a mergebot that does not answer
prints `PR state incomplete, <source>: <reason>` under the table, and `create PR` only shows when
the PR queries worked.

#### Before changing it

- The conflict check is `git merge-tree --write-tree`, which needs git 2.38; on an older git it
  is the legacy 3-argument `merge-tree` and a grep of the markers.
- `gh pr list --search involves:@me` took 4 s per repo and missed the branches of colleagues;
  `--author @me` misses the fw-bot hops. Hence GraphQL with one alias per branch. GitHub takes
  about 0.1 s per PR of a query, by number as much as by branch name, so the wait follows the
  largest query, not their number.
- Not statusCheckRollup: `ci/codeowner` fails on most drafts, and an old enterprise codeowner
  error kept a PR "running" forever, so that check is dropped first.
- legal/cla is posted once by an old batch and never again: only the FAILURES of an older batch
  are dropped, and only while a check is pending.
- Wrapping inside a cell made rich drop words: only the last column wraps, every other one has
  its longest cell as `min_width`.
- The mergebot JSON has no field for a delegation: gbs reads the last 30 comments and reviews.
- `state["building"]` of the bundle-ctl daemon covers the 2 minutes create_bundle.py takes before
  an agent is queued. `agents.py` holds the agent reading, shared with the daemon, which imports
  branch_status: gbs cannot import daemon.py.
- `main()` deletes for real, `--json` too: to look at its data without that, call
  `get_bundles()` then `get_merged_bundles()`.
- A PR branch that only exists on the remote is made local by gfa, not listed by gbs. A
  `no remote` row with commits next to an open PR in another repo of its bundle is deleted by the
  next gfa.

### open_bundle.py (`ocode`)

Opens a local bundle in VS Code, in its dev container, and marks it handled (gbs above).
`ocode -t BUNDLE` brings up the terminator window of its agent instead, and marks it handled too:
`window-picker.py --terminal BUNDLE`, which brings up the window that holds its session, minimized
or not, else opens it on its last session with `bctl open BUNDLE --terminal`, which starts a new
agent with no prompt on a bundle that has none. A bundle that is not local is fetched first with
`pfb --no-open`, then opened the same way; a pfb that fails stops it.

### bundle_open.py

Opens an `odoo-bundle://<bundle>` link: in its dev container when the worktree exists, like
`ocode`, otherwise through `pfb` in a terminal, which opens the dev container at the end.
`odoo-bundle://terminal/<bundle>`, the icon of gbs, runs `window-picker.py --agent` (bundle-ctl
README): the terminal of its agent, else its VS Code window, which then holds the session (a VS
Code window that opens closes the terminal), else a terminal on its last session or a new agent.
It marks the bundle handled, as `ocode -t` does. A bundle that is not local goes through `pfb -t`
in a terminal.
`odoo-bundle://ops` opens the ops container, like `orepo`. `odoo-bundle://ops-SESSION`, which a
shell of an ops agent window prints, also moves that session to a tab there (`bctl open
ops-SESSION`). `odoo-bundle://agent/NAME` brings up the agent of a bundle, or of an ops session
when NAME is `ops-SESSION`, as `window-picker.py --agent NAME` does (bundle-ctl README): `hok`
puts it on the name of the caller of a request, and `odoo-bundle://ops` when an ops request
carries no session.

The band of an agent terminal (`bundle-ctl/agentbar`) has three more (Seb,
2026-10-21: "a way to easily see which pending hok requests are related to this agent", "link to
open firefox group tab for all relevant bundle pages", "a button to focus the current agent in the
agent list"):

- `odoo-bundle://hok/NAME` brings up the agent list on the request NAME of hok, and
  `odoo-bundle://list/NAME` on the row of the agent NAME, a bundle or `ops-SESSION`:
  `agent-list.py --select` of bundle-ctl, which hands it to the window of hok.
- `odoo-bundle://tabs/BUNDLE` brings up the Firefox tab group of the bundle with the pages it lacks,
  or makes it: the thread and the diff of each PR `https://runbot.odoo.com/api/bundle` lists for
  it, then its page on runbot. The group is named as `firefox-tabs` names it, the icon and the name
  of the window title, and `open_group.py` of firefox-gateway opens it: the click is the yes, so no
  card waits in hok.

One-time setup, so that a click on a `gbs` icon reaches it:

```bash
$ ln -s ~/repo/useful-things/scripts/odoo-bundle.desktop ~/.local/share/applications/
$ xdg-mime default odoo-bundle.desktop x-scheme-handler/odoo-bundle
```

and add `odoo-bundle` to `terminal.integrated.allowedLinkSchemes` in the VS Code settings, next to
its default values.

## A broken worktree

**Never `git worktree prune` or `git gc`, on the host or in a container.** The base `.git` of each
repo is shared with the containers at the same path, but a container does not see the bundle
paths and the host does not see a container's `/tmp`: each side judges the other's worktrees
missing and deletes their admin entry. Bundle worktrees are locked against it
(`WORKTREE_LOCK_REASON` in `utils.py`); Claude's own worktrees are not. Remove one
registration at a time: `git worktree remove --force <listed path>` works even when the directory
is gone.

| Symptom | Cause | Recipe, on the host |
| --- | --- | --- |
| `fatal: not a git repository: REPO_ROOT/<repo>/.git/worktrees/<id>` in every bundle of one repo | a prune or gc ran where the worktree paths do not exist, the admin dirs are gone, the files are intact | recreate `.git/worktrees/<id>/` like a healthy one: `commondir` = `../..`, `gitdir` = absolute path of the worktree's `.git` file, `HEAD` = `ref: refs/heads/<bundle>`; then `git -C <worktree> reset -q`, `git -C REPO_ROOT/<repo> worktree repair`, lock again. `cat <bundle>/<repo>/.git` gives the id. `worktree repair` alone is not enough |
| pfb loops on `'<branch>' is already checked out at '<bundle path>'`, then `Unable to create ... index.lock` | a killed `git worktree add`: `locked` still says `initializing`, a 0-byte `index.lock`, NO `index` | `rm .git/worktrees/<id>/index.lock`, `git worktree unlock <path>`, `git worktree remove --force <path>`, run pfb again. Do not repair in place, do not retry first |
| pfb dies on `already checked out at '/workspace/wsf-<repo><n>'` | a test-warden scratch worktree left registered; its files are in the bundle folder (`find WORKTREE_ROOT -maxdepth 4 -name 'wsf-*'`, 1.1 GB each) | write `<host path>/.git` into `.git/worktrees/<name>/gitdir`, then `git worktree remove <path>`. pfb drops such an entry itself, the files stay |
| `git branch -D` or `delete_bundle` fails on a branch nobody uses; `git worktree list` shows `prunable` under `/tmp/claude-*` | a Claude worktree made in a container registered itself in the shared `.git` | `git worktree remove --force <listed path>`, one entry at a time: a live in-container worktree looks just as prunable |
| a bundle is gone from one repo and alive in another (refs deleted, folder, filestore and DB still there) | a `delete_bundle` cut after the first repos (one from before 2026-09-02 crashed on a dirty worktree) | `delete_bundle <name> --force` (`--also-remote` for the dev branch), then check `git -C REPO_ROOT/odoo worktree list --porcelain` and `branch --list <name>` |
| a `--no-push` bundle was deleted and no ref is left | nothing keeps it | the `create_bundle.py` command is in `STATE_ROOT/bundle-ctl/jobs/*.log`; the tip sha is in the agent transcripts (`~/.claude/projects/-workspace/*.jsonl`, any `git worktree list` or `bctl status` output, times in UTC); the scratchpad is in the old container: `podman exec -u vscode <name> tar -C <scratchpad> -cf - . \| tar -C <dest> -xf -` |

Checks that mislead:
- `podman exec` runs as root and prints `detected dubious ownership` for every repo: check as the
  vscode user (`-u vscode`).
- A recovered worktree prints the fsmonitor warning `Empty last update token` once. Harmless.
- A bundle whose branch exists in no ref, reflog or remote cannot be restored.

For a script that reads or deletes branches (both bugs aborted a whole `gfa` run):
- List branches with `git for-each-ref --format='%(refname:short)' refs/heads/`. `git branch`
  adds a pseudo-entry for a detached HEAD, `(no branch)` when the HEAD reflog is empty, and a
  mangled name without the suffix reaches `delete_bundle(force=True)`: `rm -rf` and `dropdb`.
- Before `git branch -D`, free the branch from every worktree of `git worktree list --porcelain`:
  remove a `prunable` registration, `git -C <worktree> switch --detach` a live one.

## Support modules

- **agents.py**: the bundle-ctl queue and the `.agent/` files of each bundle, read by the daemon and `branch_status.py`
- **config.py** — central configuration (repo paths, remotes, bundle suffix, sticky bundles); **edit this file to match your own setup**
- **commands.py** — pure helpers: name/path derivation for bundles, worktrees, and remotes
- **utils.py** — `Runner` subclass with Odoo-specific git operations (add/delete worktrees, fetch, branch switch)
- **command_runner.py** — subprocess execution infrastructure with live progress display and parallel runner
