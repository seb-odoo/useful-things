# bundle-ctl

Lets an agent in a bundle's dev container start other bundles, their containers and their agents.
A container sees only its own bundle folder and reaches neither podman nor the host's VS Code, so a
new bundle, its worktrees, its container and its agent only come from the host: `daemon.py` makes
them on its behalf, behind a few fixed verbs. The git work itself (fetch, rebase, cherry-pick) stays
with the agent of each bundle: github.com goes through `ssh-github-mux`, and VS Code forwards the
SSH agent into the containers it attaches to. The daemon also queues the heavy commands of every
container, test runs first, so that they never take the whole host together (see Runs).

- The daemon listens on `~/.local/state/bundle-ctl/sock/ctl.sock`. Every bundle container mounts
  that folder read-only at `/run/bundle-ctl`: a folder and not the socket, so a daemon restart needs
  no container rebuild, and read-only, so a container cannot replace the socket.
- The caller is identified by the kernel: the peer pid of the connection, its cgroup
  (`libpod-<id>.scope`), then the `devcontainer.local_folder` label of that container. A process on
  the host is `host`, and so is the container of the folder `BUNDLE_CTL_HOST_FOLDER` names, made to
  work on these tools; any other container that is not a bundle gets 403.
- One journal line per request: `journalctl --user -u bundle-ctl -f`. Also `address <ip> <name>`
  for each container that starts (and each one running when the daemon starts), the bundle or the
  container name behind an address, which the gateway logs see alone.

## Install

See the header of `bundle-ctl.service`. It runs with the odoo20 venv, which the scripts need. The
unit sets its own PATH, `~/.local/bin` first, where host-ctl puts the git that boxes the bundle
repos: a container writes their `.git`, and the jobs run git there (`daemon-git` in
`host-ctl/test_escape.py`).

## Verbs

| verb | answer |
| --- | --- |
| `GET /whoami` | the caller's bundle, base and parent |
| `GET /branches` | `gbs --json` run on the host, which deletes the bundles left with merged PRs only; `?format=table&width=N`: its colored table |
| `GET /status` | every local bundle branch, as read in the last 30 seconds (agents poll it, and a read runs git on each branch): base, behind and conflict per repo, folder, opening (launched in the last 15 minutes, container not up yet), open, and its agent: state (`running`, `waiting`, `done`, or `stopped` when its terminal or its window closed before the verdict: nothing runs it any more, and a new task may replace it; a stopped agent carries its `task` text, to queue it again as it was), when it was queued, when its run started (none for a task no window took), when it ended, `stale` when a branch of the bundle got a commit, or its dev remote ref an update (the mtime of its reflog), after that end, retry, and the last write to a session titled with the bundle, its own or an earlier agent's tab Seb went on in (`active N min ago`, or `idle since HH:MM` after 15 minutes; a tab opened by hand has no title and does not count); the queue, each task with its priority, the bundle that queued it, when, and what it waits for |
| `POST /add-repo` | `add_repo.py`: the worktree of a repo the bundle was made without (`design-themes`, `documentation`); a container asks for its own only |
| `POST /build` | the `gnb` or `pfb` a task was queued with, for the bundle whose agent started bare (see Bare start); the bundle itself or the host asks; refused when the disk is short, or when a branch of the bundle has a commit no remote has, which the script would reset |
| `POST /create` | `gnb` with `--no-push`, then a window; with a task, only its queue entry, the bundle being made when its agent asks (see Bare start); refused when the bundle exists, a branch has unpushed commits or, with no task, the disk is short (see Disk) |
| `POST /delete` | the deletion of a bundle, held for Seb's yes in `hok` (see Deletion); a container asks for its own, or for the one of a child |
| `GET /cut` | the Claude sessions a crash or a restart cut, bundles and ops (see Cut sessions); the host and the ops container only |
| `GET /held` | what became of a deletion, a task for ops or for another bundle, a build of `node_modules` or a resume this caller asked (`?id=bN`): it waits, or what Seb said in `hok`; `?wait=S` long-polls it, 50 s at most |
| `GET /hok` | the requests of `hok` that wait for the caller, a bundle its own and the host those of the ops session `?session=`: name, where a yes acts and what it does, when it was asked, put off or not (see the agent list) |
| `POST /fetch` | `pfb` on a bundle name, a PR link or a fork label, then a window; with a task, only its queue entry, as for `create`; refused when the bundle is here, a branch has unpushed commits or, with no task, the disk is short |
| `POST /node-modules` | the `node_modules` of `base` built again from the `package.json` of the web tooling of its branch, with the lock in use (see `scripts/README.md`, Shared node_modules): a job for the host; from a bundle, for its own base only, held as `bN` for Seb's yes in `hok`, which shows the change of `package.json` |
| `POST /open` | a VS Code window on a bundle folder, or with a task its agent, in the bundle's last session with `resume`, or with `terminal` the agent terminal on that session and no prompt, taken from its VS Code window when the bundle itself or the host asks; refused on a half-made bundle, but for `resume` or `terminal` in a bare folder, whose session knows how to build it. A task from a bundle that is neither that bundle nor its parent waits for Seb's yes as `bN` (see A task for another bundle). On `ops`, from the host or the ops container only: the ops window, or with a task or `terminal` a new ops agent window, and from a bundle a task alone, held as `bN` for Seb's yes in `hok`; on `ops-SESSION`, that session in an ops agent window with `terminal`, or alone in a tab of the ops window (see Ops agents). With `cut` and `resume` and no bundle, the resume of every cut session, held as `bN` for one yes of Seb (see Cut sessions) |
| `GET /job` | the state and log of a create/fetch/open, long-polled by `bctl` |
| `GET /queue` | the queue of `GET /status` alone, at once: the agents at work in a terminal, the cap, each task with what it waits for, its text, whether it resumes the session of its bundle and the session it names, and the memory left on the host, `short` when a start would add to a host that already lacks it |
| `POST /force-queue` | the tasks queued now may start over the cap, those that wait for their time (`--after`) or for a manual resume (`--paused`) aside, or with `bundle` the task of that bundle, its time and its pause too; then the answer of `GET /queue`, with the tasks it forced and the ones that started. Refused to a container |
| `POST /move` | the queued task of `bundle` put at `place` (from 1), where it keeps its priority when that priority fits between the tasks around it, else takes the nearest of theirs; then the answer of `GET /queue`. The host, that bundle or the one that queued the task asks |
| `POST /requeue` | the session of the agent of `bundle` queued again, last, paused with `paused`, and its agent window closed once its turn ended, 2 minutes at most: meanwhile `client/run-hook.py` refuses its Bash calls, on a reason that tells it to end its turn, and a turn still running at the end goes on told it was cut (see Cut sessions). With `github`, it waits for the GitHub connection of the host. The host or the ops container asks, or with `github` the bundle itself |
| `POST /schedule` | when the queued task of `bundle` may start: `when` is `spot` (once a slot, the memory and the CPU let it), `manual` (it waits for a manual resume, until it is scheduled again) or `HH:MM` (that time of the host, today or tomorrow); it no longer goes over the cap. Then the answer of `GET /queue`. The host, that bundle or the one that queued the task asks |
| `POST /unqueue` | the queued task of a bundle dropped; the host, that bundle or the one that queued the task asks |
| `POST /run` | a heavy command waits for cores: one JSON line each time its place changes, then the CPUs it got (see Runs) |
| `GET /runs` | the runs that hold cores and the ones that wait, the pool and the memory available |

A `create`, a `fetch` or a `build` that fails leaves a half-made bundle: a folder with no
`.made`, which the scripts write last, after the config files of `enable.sh`. `open` refuses it,
and the same command sent again finishes it. A restart of the daemon kills its jobs, which run in
the cgroup of the unit: `bctl build` then asks its build again once the daemon is back, and prints
`a restart of bundle-ctl killed the job, asked again`; the other verbs say to send them again. On
2026-10-21 a deploy killed a build in `enable.sh`, which `.made` then preceded: enterprise got no
`package.json`, the agent no word, and the pre-commit hook of Odoo removed the `node_modules`
links of the bundle. `python3 test_queue.py` and `scripts/test_optional_repos.py` have the cases.

`add-repo`, `create`, `fetch` and `open` answer 202 with a job id, and the jobs run one at a time, as two
fetches of one repo collide on its remote refs. The jobs of one bundle also wait for each other. The
agent terminal of a bundle (`open --terminal`, the start of a queued task) waits only for the jobs
of its bundle, and the ops window and the ops agent windows only for each other: neither waits
behind the build of another bundle. A job takes the `DISPLAY` and `SSH_AUTH_SOCK` of the desktop
session from `systemctl --user show-environment`, so it fails until Seb is logged in. The daemon
never pushes.

`bctl` (`client/bctl.py`, stdlib only, defined in `container-rw/devcontainer.bashrc`) calls them
from a container or from the host: `bctl whoami`, `bctl status`. The containers mount `client/`
read-only at its host path. Without the client:

    curl --unix-socket ~/.local/state/bundle-ctl/sock/ctl.sock http://bundle-ctl/whoami

## Runs

Agents that each start a test run, an install or a server when they see fit take every core at
once and push the host into swap. So the heavy commands of the containers go through one queue of
the daemon, which gives each of them cores of its own.

- The pool is every core of the machine but the first third, which stays to the desktop.
  `BUNDLE_CTL_RUN_CPUS` (a CPU list such as `4-11`) names other ones. A core is given whole, with
  all its threads.
- `bctl run [--kind KIND] [--workers N] -- COMMAND` asks for a run, waits for its start, pins
  itself to the CPUs it got (`sched_setaffinity`) and becomes COMMAND. Without COMMAND the run is
  the shell that called it: `bctl run` pins that shell and returns, and what the shell starts
  next inherits its CPUs.
- A run holds its cores until its process ends, which the daemon checks every second. No run is
  killed and none has a time limit: `bctl runs` shows since when each one runs. After an hour a
  run lends its cores, so the next one starts on them too: a run nobody ends (a `twc` without
  `--once`) must not stop the queue.

| kind | what | cores | memory counted |
| --- | --- | --- | --- |
| `hoot` | test-warden | one per worker, 2 at most | 1 GB, plus 1.3 GB per worker |
| `odoo` | an `odoo-bin` that tests or installs | 1 | 2 GB |
| `other` | any other command | 1 | 1 GB |
| `server`, `shell` | an `odoo-bin` that serves, `odoo-bin shell` | none of its own: the whole pool, at once | 0.6 GB |

- Runs start in arrival order, and a run that waits holds back the ones behind it.
- While another run holds cores, a run also waits when its start would leave less than
  `BUNDLE_CTL_RUN_MIN_FREE_GB` (default 4) of available memory or take more than the containers
  have left under their limit (see Memory), the runs started in the last minute counted at what
  the table says, or when the host stalls on memory (`some avg10` of `/proc/pressure/memory` at 5
  or more). The first run always starts.
- A client that leaves the queue before its start (a call that timed out) finds its place again
  when the same bundle asks for the same command within 15 minutes.
- A command started by a run that holds cores gets the same ones, and no entry of its own.
- The runs are kept in `runs.json` beside `state.json`: a daemon that restarts takes back the
  ones whose process still lives, and a client that waited asks again by itself for a minute.
- When the daemon does not answer, or is older than the client, the command says so and runs
  without the queue.

An agent does not call `bctl run`. `client/run-hook.py`, a PreToolUse hook of the shared bundle
`.claude/settings.json`, writes it as a line in front of a Bash command that runs `odoo-bin` or
test-warden (`engine-linux-*`, `test-warden.cjs`, and the helpers `twc`, `ott`, `otf`, `obet`),
whether directly, behind `nice` or `timeout`, from a variable, in `bash -c` or in a script file.
It reads the kind in the flags (`--stop-after-init`, `--test-tags`, `-i`, `shell`, `--workers N`).
The agent writes its command as before, and reads on stderr where it waits:

    bctl run: waits for 1 core, place 2 in the queue. Running: master-a--seb (hoot, 6 min).

The wait counts in the timeout of the Bash call. Claude Code moves a foreground call that
outlives it to the background and stops it 10 minutes later, so a long run is started in the
background. A command typed in a terminal passes no hook: what Seb runs himself never waits.

The journal has one line per start and per end, `run started BUNDLE KIND cpus=4-7 waited=12s` and
`run done BUNDLE KIND ran=95s cpu=3.1`, where `cpu` is how many CPUs the container of the run kept
busy on average: read it before changing the cores of a kind. `python3 test_runs.py` runs the
hook on agent commands, the order of the queue, and real clients on a socket of their own.

## Memory

The containers sit in one cgroup, the `user.slice` of the user manager. It has `MemoryHigh=16G`
and `CPUWeight=20` (`systemctl --user set-property`, kept in `~/.config/systemd/user.control/`):
above 16 GB the kernel swaps the pages of the containers before the desktop's, and kills nothing;
when the CPU is short, the desktop gets it first. The CPU weight needs the `cpu` controller in the
user manager, which Ubuntu 22.04 does not delegate: the root drop-in
`/etc/systemd/system/user@.service.d/delegate.conf` (`Delegate=cpu cpuset io memory pids`) gives it
from the next boot.

An agent starts only when 2 GB fit in what is left, and a run when what the Runs table says
fits. What is left is the smaller of:

- what the containers have left: 16 GB minus what they hold besides their file cache (the
  `memory.current`, less `file`, plus `shmem` of the slice); what they swapped out does not count;
- `MemAvailable`, less the 4 GB kept free.

Less what already started and has not taken its memory yet: for an agent, 2 GB for each agent
that began its run in the last 10 minutes, as its install and its tests come later; for a run,
what the table says for each run in its first minute. And nothing starts while programs stall on
memory (`some` of `/proc/pressure/memory`) 5% of the last minute for an agent, of the last 10
seconds for a run.

An agent also waits while programs wait on the CPU (`some` of `/proc/pressure/cpu`) 20% of the
last minute, `AGENT_CPU_STALL` (Seb, 2026-10-23): memory alone let 6 agents start in 5 minutes
while the load was 43 on 12 threads. `bctl queue` then says `waits for the CPU of the host`.

`MemAvailable` alone is not enough: it counts the page cache, and read 12 GB on 2026-10-15 while
the host had 9 GB in swap and stalled. Every minute the journal has a line such as
`memory available=11.8 ramping=1 room=0.4 stall=19.17 swap=16.1`: read it before changing one of
these numbers. Since 2026-10-23 it also gives `cpu=`, and ends on `short` or `busy` when an agent
would wait.

## Disk

A bundle takes about 3 GB at once, so agents that each make one can fill the disk in minutes. The
daemon reads what is left on the disk of the bundles:

- Under `BUNDLE_CTL_DISK_STOP_GB` (default 30), `create` and `fetch` with no task and `build` are
  refused, and the queue holds its tasks, forced ones too: `waits for disk space (20 GB left, 30
  needed)`. They start by themselves once the space is back.
- Under `BUNDLE_CTL_DISK_WARN_GB` (default 60), a desktop notification says what is left, then
  again at each 10 GB less, and again once the disk went 10 GB over that line in between. The
  level warned is kept in `~/.local/state/bundle-ctl/disk-warned`, so a restart of the daemon
  does not warn again.
- `python3 test_queue.py` has the cases.

A bundle of someone else's PR (no `BUNDLE_SUFFIX` in its name) goes by itself once nobody used it
for `BUNDLE_CTL_REVIEW_CLOSED_MINUTES` since its container stopped (default 10, Seb's choice on
2026-10-14: out of the way once its window or terminal closed), or for
`BUNDLE_CTL_REVIEW_IDLE_DAYS` when no container ever ran for it (default 2, Seb's choice on
2026-10-13): no window open, no task queued or being built, and no file newer than that among its
`.made`, its `.agent/` files, its Claude config (written at each window open and Claude start) and
the reflog of its worktrees. The daemon looks each minute and runs `delete_bundle.py`, unless the
bundle was used while the job waited for another one. `delete_bundle.py` keeps a bundle with
uncommitted files or a commit no remote has; that one is tried again only after a new use. Commits of others no remote has (the older
head of a PR its author rebased since) and commits whose change the base has go with `--force`,
unless a file is not committed. `bctl fetch` brings a bundle back.
`python3 test_delete.py` has the cases.

A task that is the session of its agent waiting for a manual resume or for its time (`bctl
requeue`, Pause in the agent list, `bctl open --resume` with `--paused` or `--after`) does not keep
such a bundle: its checkout goes the same way, and the task keeps its session and `fetch_bundle.py
BUNDLE --no-open` as its build (Seb, 2026-10-19: close the review agents that wait for him, "in a
way where they free their disk"). When it leaves the queue, the agent goes on with that session in
a bare folder (see Bare start) and fetches the bundle with `bctl build` if it needs the code. A
resume waits while the deletion runs. A bare folder with such a task is kept, as its `.agent/`
holds the session. `python3 test_delete.py` and `python3 test_queue.py` have the cases.

A bare folder (an agent and the links of a bundle, no worktree: its agent never asked `build`) goes
the same way, whatever its name: the daemon removes the folder. The `tasks` folder where its agent
wrote the tasks it queued (bundle-ctl skill) keeps it bare.

## Bare start

A bundle takes minutes and about 3 GB to make, and many tasks need none: a PR closed since, a
review that reading the diff settles, a bug already fixed. So `create` and `fetch` with a task
only queue it, with the script that would make the bundle (Seb's choice on 2026-10-15). When its
turn comes, the daemon prepares the folder (`.agent` and the links, no worktree) and starts the
agent there, its task opened by a note: nothing is checked out, the base repos are read through
their `.git`, GitHub through `gh`. The agent drops the task or does it from there, or runs `bctl
build`, which makes the bundle in that folder, and goes on in the same session. Until then the
queue says `not built yet` and `status` lists the bundle with no repo. `python3 test_queue.py` has
the cases.

## Wrong version

`client/check-base.sh` is a SessionStart hook of the shared bundle `.claude/settings.json`: when
the open PR of the bundle targets another base, it tells the session to `bctl create` a bundle on
that base with a task to cherry-pick the commits. The agent of the new window does the
cherry-pick, as all the bundles share the base repos' `.git`.

## Deletion

`bctl delete [BUNDLE] --reason TEXT` lets an agent whose work moved to another bundle ask for its
own to go, where it would hand Seb a `delete_bundle` line to type. A bundle asks it too for its
children, the bundles whose last task it queued (their `.agent/parent`): the ones it started for a
measure, say. It is refused while a branch of
the bundle holds work: a commit of Seb no remote has, a branch on the dev remote that no PR uses
yet, or an open PR. A branch whose PRs are all closed or merged goes, even with commits of others
no remote has: in a review bundle, the older head of a PR its author rebased since. A commit whose
change the base of the bundle already has under another sha (`git cherry`, same patch-id) is no
work: a branch never pushed whose commits are all in its base goes too, with no PR, as the copy
of a fix that reached master by a forward-port.

`--drop COMMIT` (7 characters of its sha or more, once per commit) names a commit of Seb no remote
has that the deletion may lose: the request lists it with its subject, and his yes loses it. A
commit named that the branch does not hold, or one a remote has, is refused. The yes checks the
branches again with the same commits, so a commit made after the request still keeps the bundle.

- The daemon holds the request in `held/` beside `state.json`, in the format of
  `gateway-core/held.py`, and `hok` lists it as `bN` (the `config.py` of host-ctl names the
  folder). No container mounts that folder: who asks cannot answer. Without gateway-core beside
  useful-things, the verb is refused.
- A yes checks the branches again, then runs `delete_bundle.py BUNDLE` as a job, at once when the
  container of the bundle stopped, else when it stops: `deletions` in `state.json` holds the
  bundles that wait for that. It also drops a task queued for the bundle, which the stop of its
  container would start. A task queued after the yes drops the deletion instead: the bundle has
  work again, and its agent asks again once done. On 2026-10-16 such a task started a new agent
  while the deletion ran, which removed the worktrees under it. `delete_bundle.py` removes the
  container before the worktrees: a container started meanwhile makes it fail before anything goes.
- The yes closes the agent window of the bundle once no claude of the bundle is in a turn, as
  the newest record of each live session says (not `idle` nor `shell`: busy, or waiting for an
  answer), so the bundle goes with no click (Seb, 2026-10-20: "shouldn't this terminal close
  automatically when the bundle is deleted?"). An agent that asked its deletion in its turn ends
  that turn on its report first. The check runs at the yes, at each stop of a container and at
  each pass of the queue (every minute at most). A VS Code window on the bundle gets
  `.agent/close-window`, and a container that runs with no window left is stopped: the stop
  starts `delete_bundle.py` while the folder is still there, which the shutdown monitor of VS
  Code needs. When the session of its agent window already ended on the report of its verdict,
  the request shows that report.
- The last answer of the agent (the last text of its session, else its `result.md`) is copied
  to `deleted/BUNDLE.md` beside `state.json` before `delete_bundle.py` runs, and the answer of
  the yes names that file.
- The yes also closes the Firefox tab groups of the bundle (Seb, 2026-10-20), which the request
  lists: the helper of firefox-gateway answers on `$XDG_RUNTIME_DIR/firefox-gateway/host.sock`
  (`scripts/tab_groups.py`). `delete_bundle.py` closes them too, so a bundle deleted by gbs, gfa
  or by hand loses its groups as well. With no helper, Firefox closed say, there is nothing to
  close.
- `--wait S` waits that long for his answer and prints it, as `hctl --wait` does: exit 0 on his
  yes, 126 when he refused, 75 when it still waits. The same delete asked again replaces the
  request that waits, under the same `bN`, and waits for it.
- `delete_bundle` still refuses uncommitted files: the job log in `jobs/` says so. Commits of
  others need its `--force`, which drops such files too: the request is then refused on them.
- `python3 test_delete.py` runs each case on git repos of its own.

## A task for another bundle

The agent of a bundle runs its task in auto mode, and git-gate lets it push its own branch with no
yes. So a task on a bundle that is there already (`open`, and `create` or `fetch` of a folder that
holds an `.agent`), `--resume` included, goes to the queue with no yes only from the host, from
that bundle, or from its parent, the bundle that queued its last task (`.agent/parent`). From any
other bundle, a review bundle on the PR of a stranger say, the daemon holds it as `bN` for Seb's yes
in `hok`, opened by "The agent of the bundle X sends this task". His yes queues it as from the
parent: the sender does not become the parent, and its next task waits for a yes again. The same
task sent again replaces the request that waits. `python3 test_open.py` has the cases.

## Agents in a terminal

A task (`.agent/task.md`) runs with Claude in the bundle's container, without a VS Code window: an
agent window holds 1.1 to 1.8 GB in its container plus about 650 MB of VS Code on the host, an
agent in a terminal about 250 MB. The daemon starts `agent-terminal.py BUNDLE` in a terminal
(`AGENT_TERMINAL`, gnome-terminal by default):

- It creates or starts the container with the `up` of the devcontainer CLI that the Dev Containers
  extension ships, with the arguments of that extension, on the bundle's devcontainer.json. So the
  sandbox has one definition, and VS Code later finds the container by its two labels.
- It copies `~/.gitconfig` into a container that has none, as VS Code does when it attaches: git
  has no identity there otherwise.
- It runs the interactive `claude` in the container (`client/agent-run.sh --terminal`), on the task
  as its prompt, in `auto` like the `claude -p` of an agent window. Seb can type in that terminal.
  Both run on `opus` at `medium` effort, unless its task asked for another model or effort. Never
  on `default`, the default of the account, which Anthropic sets and which is not the
  `ANTHROPIC_MODEL` of the settings.
- That terminal runs claude with `prefersReducedMotion` (Seb, 2026-10-23): its spinner stops
  moving, the seconds still count. On 2026-10-22 the 20 agent terminals took 3 of the 12 CPUs
  to draw it, more than the tests they waited on, and a waiting claude went from 10 redraws a
  second to 3, and from 12% to 7% of a CPU. His VS Code tabs and the ops windows keep the
  animation.
- A turn that ends on a verdict writes `done` (`client/agent-stop.py`), a turn that ends on
  anything else `waiting`. A `waiting` agent keeps its slot of the cap, its container, and its
  session open in its terminal, for Seb to read what ran and to type in. A `done` agent leaves its
  slot, and the daemon closes its terminal a few minutes later (below), so that its container
  gives its memory back.
- A turn that ends on an API error that a new request may pass (`server_error` in the transcript:
  a 5xx, the API out of reach, an answer cut) writes neither, as no Stop hook runs then. Claude
  has tried again for 4 minutes by then: on 2026-10-21 the network of the host was down 15
  minutes, and 9 agents kept their slot on that error until Seb saw them (his ask, 2026-10-22:
  "either auto retry or drop the agent to be queued later to free a slot"). The terminal types
  `Continue from where you left off.` once the session has been idle 5 minutes on it
  (`RETRY_AFTER`), as it types a PR event, for 5 such turns in a row at most (`RETRIES` of
  `client/window-title.py`). On the sixth, the daemon puts the agent back in the queue as Back to
  queue does, while its window is minimized and before its verdict: its slot goes to the next
  task, and the queue starts the same session again later. The journal says `BUNDLE: 6 turns in a row ended on an API error,
  back to the queue`. A usage limit is not such an error: claude goes on at its reset.
  `python3 test_terminal.py` and `python3 test_restart.py` run each case.
- Quitting claude or closing the terminal stops the container. Before the verdict nothing else
  does: Seb may type in that session at any time. Without a verdict the agent then reads `stopped`. So does a
  terminal that dies without closing (killed, or lost with the X server): `podman exec` does not see that hangup, so the wrapper ends on the hangup and stops
  the container itself. With a VS Code window attached, it keeps the container and hands the
  session to that window instead (`client/agent-takeover.py`, as the window does when it opens):
  else its claude runs on with no terminal, and the tab finds the session open in another process.
- Quitting claude or closing the terminal also closes the Firefox tab groups of the bundle (Seb,
  2026-10-20: "closing an agent or deleting a bundle should also close its firefox group"). Not
  when the daemon closes the window for Back to queue, Pause or a verdict (`.agent/pause`), on a takeover, on
  a claude that ended on an error or a container that could not start, nor while the host shuts
  down (`systemctl is-system-running` says `stopping`), so that Firefox restores them.
- Once its agent gave its verdict, the daemon closes the terminal (Seb, 2026-10-21: "bundle agents
  shouldn't wait for their own ci or something, this is done with triage. once work is done,
  agents should free up slot"). The CI, the staging and the reviews of its PRs are for the next
  triage, which queues a new task: PR events (below) no longer reach a closed agent. Only when the
  last answer of its session opens on a verdict and its session has been `idle` for 5 minutes
  (`FREE_AFTER`, so that Seb sees its ✅ or ✋ and can bring it up): not in a turn he started, a
  dialog, a background command a turn left (`shell`: the watch of a review that waits for his
  review), nor the reset of the usage limit. Only while its window is minimized, as one on screen
  is his to read, and while no task is queued for the bundle, which goes on in that session
  (below). Its claude and its container stop as when Seb closes it, but its Firefox tab groups
  stay: they hold the pages the report asks him to open. The report stays in `.agent/result.md`
  and in the transcript, the agent reads `done`, and `bctl open BUNDLE --terminal` opens the
  session again. The journal says `BUNDLE: its agent ended on its verdict, its terminal closes`.
  `python3 test_restart.py` runs each case.
- A terminal runs the code it started with. When `agent-terminal.py`, `agent-terminator.py` or a
  script they load changed since, the daemon opens a new terminal on that bundle, which takes the
  session over in the same container, and the old window closes (Seb, 2026-10-20: "can we somehow
  restart them gracefully"). Only when its session is `idle`: not in a turn, nor in a background
  command a turn left (`shell`, a PR watch), which would end with its claude, nor the reset of the
  usage limit, which its claude goes on from by itself and a new one does not. Only while its window
  is minimized, so that Seb sees no window go, as the new one opens minimized; and not under
  `.agent/pause`. One terminal per pass of the daemon (10 to 60 seconds), each bundle once an hour
  at most; the journal says `BUNDLE: its terminal restarts on the code of HH:MM`, and the new
  window, before its claude resumes, `BUNDLE: restarts on the bundle-ctl code of HH:MM, same
  session` (Seb, 2026-10-21, who took the old line for a second terminal). A task given to an idle
  session says `goes on with the task from PARENT, same session` there. Ops windows stay as they
  are. `python3 test_restart.py` runs each case.
- `bctl open BUNDLE --terminal` opens that terminal again, on the bundle's last session and with
  no prompt: the way back once the X server took the windows away. It is refused while a terminal
  holds the session: a new terminal would take it over and cut its turn. A VS Code window that
  holds it gives it back (below). On a bundle with no agent, asked by the host (`ocode -t`), it
  starts a new agent there: a new session, an empty `task.md`, and claude with no prompt, in a
  window that shows in front, as an ops agent with no task.
- What Seb selects with the mouse in claude, and what `/copy` copies, reach the clipboard of the
  host (Seb, 2026-10-15). claude sends it as an OSC 52 escape, which VTE ignores, so the wrapper
  runs `podman exec` in a pty of its own, takes the escape out of the output and gives its text
  to `xclip`. Only while the window of the terminal has the focus (its `_NET_WM_PID` is the parent
  of the wrapper, its terminator), so that a process of the container cannot fill the clipboard
  behind Seb's back; a query of the clipboard gets no answer. claude sends the escape only while
  it finds no `xclip` in the container and no `VTE_VERSION` in its environment.
- A claude that ends on an error leaves the terminal open on its exit code and on the tool calls
  and answers of the session (`client/stream-format.py` on its transcript): claude draws on the
  alternate screen, which empties when claude ends.
- When no container could start (a failed `up`), the terminal stays open on the reason, and the
  wrapper writes it to `result.md` with `done` as `failed`: `bctl status` shows it to the bundle
  that queued the task, and the launch gives its slot back at the next check of the queue.

With `AGENT_TERMINAL` set to `agent-terminator.py {bundle}`, the window is a terminator window
that holds the agent alone, maximized, in a pane titled `in the container: claude` (Seb,
2026-10-20: the host shell next to it, there since 2026-10-09, went unused). The shell to type in
is the contained terminal of the VS Code window.

- A pane split by hand (Ctrl+Shift+O, Ctrl+Shift+E) and a new tab (Ctrl+Shift+T) are shells of
  the host, as in any terminator window. Such a shell starts on `On the host, outside the
  container of BUNDLE.` and on `ocode BUNDLE opens its VS Code window.`, which carries the same
  link as `gbs`: the launcher exports `BUNDLE_CTL_AGENT_WINDOW`, and `.bashrc` prints the two
  lines in an interactive shell that holds it.
- The launcher runs terminator on a copy of `~/.config/terminator/config`, in
  `$XDG_RUNTIME_DIR/bundle-ctl/`, with a layout added that runs the agent: preferences saved from
  an agent window go to the copy. Not with `-x`: the command line of terminator would then hold
  `agent-terminal.py BUNDLE`, which the process scans take for the agent itself.
- When claude quits, its container stops and the window closes, unless a pane split by hand still
  runs. A takeover by a VS Code window or another terminal closes the whole window (Seb,
  2026-10-13): the wrapper sends SIGUSR1 to `agent-terminator.py` (`BUNDLE_CTL_WINDOW_PID`), which
  ends its terminator.
- With wmctrl, the window opens minimized (Seb, 2026-10-14), so that a new agent does not take
  the screen: it waits in the window list (below). Terminator maps it minimized with `--hidden`,
  so that it never shows first. It does so only while its `hide_window` key is not bound (with the
  key bound, `--hidden` keeps the window unmapped until that key): the copy of the config sets
  `hide_window = None`.
- Shift+Enter starts a new line in claude, as in its VS Code tab. VTE sends the same carriage
  return for Enter and Shift+Enter, so the copy of the config enables `shift_enter_newline.py`, a
  terminator plugin that sends a line feed (Ctrl+J) instead: a shell runs the line on it, as on
  Enter. The launcher links it into `~/.config/terminator/plugins/`, where terminator looks for
  plugins; the other terminator windows of Seb do not enable it.
- The window title tells the windows apart in the task bar: an icon of what the work is about,
  the bundle, then that kind in words, as `📝 master-action-panel-aku (review)`. The word comes
  last, so that a narrow task bar button still shows the bundle. `client/window-title.py BUNDLE`
  reads the kind from the name alone. claude-autoopen gives the same title to the VS Code window
  of the bundle.
- In front of that icon, a mark says what Claude does in the window, so that the task bar shows
  which agents work, which are done and which wait for Seb: `⏳ 📝 master-action-panel-aku
  (review)`. It comes from the `status` Claude writes for each live session in
  `~/.claude/sessions/<pid>.json`, a folder of each container (`devcontainer/README.md`), and for
  an idle one from the close line of its last answer (Seb, 2026-10-16): the line a report ends on,
  `Nothing left` or `Waiting for you`, with only `Separately:` lines after it, as the Stop hook
  `~/.claude/hooks/guard-report-end.py` wants it, its variants included (`Nothing is left`,
  `Waiting on you`, `Waits for you`). An answer with no close line, older or too short to be a
  report, gives the mark by its first line, as before. A session whose turn ended while a
  background command runs is `shell`, and counts as idle: a review agent ends on `needs Seb:` and
  leaves `thread-watch.py` waiting for his review (Seb, 2026-10-14: they showed ⏳ while they
  waited for him). A busy session whose running call waits for Seb's answer in hok, a
  `hok-answer` or an `hctl job` that the tail of its transcript shows with no result yet, waits
  for him too (Seb, 2026-10-22: "agents that waits on hok should be in waiting for me state, not
  in running"). `client/window-title.py BUNDLE SESSION=STATUS...` picks it. With wmctrl, `agent-terminator.py`
  opens the window on 🚀 and follows the session of its claude every second until the window
  closes, and keeps the last mark once that claude ended; a busy session is read again every 10
  seconds, as such a call changes the mark and not the status. claude-autoopen follows the
  sessions of its container, on a change of status only.
  `python3 test_window_title.py` runs each case.

| icon | kind | bundle name |
| --- | --- | --- |
| ⏩ | fw | ends with `-fw`, a fw-bot hop |
| 📝 | review | does not end with `BUNDLE_SUFFIX`: the branch of someone else |
| 🤖 | runbot | `-runbot-N` or `-flake` before the suffix |
| 🎯 | task | an odoo.com task id (7 digits or more) before the suffix |
| 🧠 | analysis | `-code-analysis` before the suffix |
| ⭐ | mine | any other name with the suffix |

| mark | the sessions of the window |
| --- | --- |
| 🚀 | an agent window whose claude did not start yet: its container starts |
| ⏳ | one works: `busy`, out of a call that waits for hok, or waits for the reset of the usage limit, as Claude then goes on by itself (its last notice is `Usage limit reached · continuing automatically at 4pm`, not cancelled by Esc, for 5 hours at most: a claude started later on that session does not go on by itself) |
| ✋ | one waits for a permission or an answer (`waiting`), or for Seb's answer in hok (`busy` on `hok-answer` or `hctl job`), or none works and one last answer ends on `Waiting for you`, or has no close line and is no `pushed:` nor `nothing to do:` (`ready to push`, `needs Seb:`, `blocked:`, a chat) |
| ✅ | none works and all last answers end on `Nothing left`, or have no close line and are `pushed:` or `nothing to do:` |
| ❌ | the run of an agent window ended before its claude started (`.agent/done` is `failed`) |
| none | no live session |

A minimized agent window leaves the task bar and Alt+Tab, and comes back when it is restored:
25 agent windows left the task bar no room to show a title. To reach them:

- `window-picker.py`, a rofi script, lists the bundles first by mark (❌, ✅, ✋, ⏳, 🚀), then by
  kind (ops, fw, runbot, task, mine, analysis, review: Seb, 2026-10-15), one row each, then the
  other windows. A ❌ comes first, as an error to fix, then a ✅ or ✋ ops, fw, runbot or mine
  agent, by kind then by mark, as it waits for him (Seb, 2026-10-15): a ✋ fw comes after a ✅ fw,
  before a ✅ runbot. A
  bundle open in VS Code has no row (Seb, 2026-10-13): he works there, and its window is in the
  task bar. Nor has a bundle or an ops agent whose terminal is not minimized (Seb, 2026-10-14): it
  is in the task bar too, and the applet does not count it. A
  terminal keeps its last mark once its claude ends, so a ✅, ✋ or ⏳ shows only while an
  `agent-terminal.py BUNDLE` process runs. A claude that ends on an error leaves its window on
  that error until Enter (`keep_open` in `agent-terminal.py`): the row then shows ❌ in place of
  the mark of the title (Seb, 2026-10-19: such ops windows looked like agents at rest). The picker
  finds the `sh` that waits in the pane, a child of the terminator of the window. After the title, a row shows the close line of the last
  answer of its session (Seb, 2026-10-16: he had asked "status?" 99 times and "can I close?" 147
  times), `Waiting for you: hok g12 for the 19.0 hop` say, cut by rofi to the width of its window:
  the session in `.agent/session` for a bundle, the one in the name of an ops window. The window
  title does not hold it, as the close line often holds a link, and the picker and the list find
  the kind at the end of the title. The search also
  takes the mark in words (`finished`, `waiting`, `failed`, `running`, `starting`), the kind is
  already in the title, and the line above the rows counts the bundles of each mark.
  `window-picker.py --show` opens it in rofi, where the row under the mouse is selected: a click on
  a bundle brings its VS Code window up, or opens it through its `odoo-bundle://` link, and a right
  click or Shift+Enter brings its terminal up. Until that VS Code window is up, the row of the
  bundle shows 🔄 (a file per bundle in `$XDG_RUNTIME_DIR/bundle-ctl/opening`, 5 minutes at most,
  dropped once the window is up). A Cinnamon shortcut runs it. `window-picker.py --agent BUNDLE`
  brings up the terminal of its agent, else its VS Code window, which then holds the session,
  else starts that agent again in a terminal (`bctl open BUNDLE --terminal`) and brings the window
  up once it shows; `--terminal BUNDLE` does the same without the VS Code window, which bctl takes
  the session from (`ocode -t`, Seb, 2026-10-20: "I'm expecting this to unminimize the terminal
  so I can see it"); `--code BUNDLE` is the click on its row. The helper of firefox-gateway runs
  them from a tab of the Firefox group of the bundle, and `bundle-info.py BUNDLE` for the bundle
  tab first in that group (Seb, 2026-10-20: "as much info as possible [...] or even better the
  conversation itself"): JSON with the session of its agent, the close line of its last answer,
  and its last 120 prompts, answers and tool calls. `--agent ops-SESSION` brings up the ops agent
  window of that session, else the ops window when a claude of the ops container runs the session
  out of an agent window (in a tab), else goes on with it in a new agent window (`bctl open
  ops-SESSION --terminal`). `hok` links the caller of a request there (Seb, 2026-10-20: "a direct
  link to open the agent from which they come from"), through `odoo-bundle://agent/NAME`.
- `agent-list.py --select KEY` brings the window up on the row of KEY once its rows show it, 10
  seconds at most: a bundle, an ops agent window by its session (`ops-SESSION`), or in the window
  of `hok`, which it hands the key to, a request (`hok:NAME`). The band of an agent terminal links
  it (Seb, 2026-10-21: "see which pending hok requests are related to this agent + link to
  open+focus this hok request on the app", "a button to focus the current agent in the agent
  list"), through `bundle_open.py` (scripts README), and reads the requests of its agent with `GET
  /hok`: `hok waiting` of host-ctl, read again after 5 seconds at most, as every agent terminal
  asks, then kept to the bundle that asks.
- `agent-list.py`, a GTK window that stays open (Seb, 2026-10-15: "a new display for agents list
  that I can keep open and that updates live"), has the rows of the picker, read again every 2
  seconds, the terminals shown on screen included, and no other window. The mark and the kind of
  a row have columns of their own (Seb, 2026-10-15), and the title keeps the bundle, or the title
  of the conversation of an ops window, cut at 40 characters, as any column but the last ones
  (Seb, 2026-10-20: "the agent column is too long [...] I don't see the others after"); a drag
  of its edge widens it. A click on any column header sorts the rows by it (his
  ask: "all of them"): the mark in the order of the picker, which a first start shows, the kind
  in its order, the times and the sizes as numbers. A bundle row also has the
  state of its agent and since when, from its `.agent/` files (`done 12 min ago`, `waiting for 5
  min`, `running for 3 min`, then `shown` when its terminal is not minimized). An ops row has it
  from the record of its session in the sessions folder of the ops container (Seb, 2026-10-21:
  "ops agent don't have anything in the since column"): running in a turn, waiting on a dialog or
  when its mark says it waits for Seb, else done. A row whose call waits for Seb's answer in hok
  is `waiting for 5 min` from the first of such calls in a row, a bundle one while its mark is
  ✋, as the mark says it. A row whose claude
  ended on an error, a bundle or an ops one, has that error there: `claude ended with code 137`.
  Each row has the close line of the last answer of its session in its last column, else the
  first line of that answer, as an agent at work has no close line yet (Seb, same day: "no last
  answer"), read again only when the transcript changed; a bundle row with no answer has the
  first line of its `result.md`. Each row has its CPU and RAM (Seb,
  2026-10-15), read every 5 seconds: those of the container of a bundle, from its
  `libpod-ID.scope` cgroup under the user manager, the RAM
  without the file cache as the run queue counts it; those of the claude of an ops window and its
  children, as the ops agents share one container. 100% is one core. The disk of a bundle is a
  `du` of its folder when its row comes, then every 10 minutes: its `node_modules` counts there,
  though they are hard links of the base. The Tabs column of a bundle has the icon of each of its
  Firefox tab groups (Seb, 2026-10-20), read with the rows from the helper of firefox-gateway
  (`scripts/tab_groups.py`), then, when they all look finished, why: `🚀👀 PR merged, PR
  opened`. Buttons above the rows count each mark among the kinds
  pressed, and each kind among the marks pressed (his ask: the counts followed no other filter);
  pressed, they keep the rows of one of the marks pressed and of one of the kinds pressed: the
  buttons of a line add up, and the two lines cut each other. The
  Branch search beside them (Seb, 2026-10-20: "to look for a branch name (both now and queue)")
  keeps the rows whose name has its text, the groups of `hok` included, and the tasks of the
  queue whose bundle has it, case
  aside. Its text loses a dev remote prefix (`odoo-dev:`), as for `ocode` (Seb, same day); the
  buttons count only the rows it keeps, and Up and Down move a task past the one shown
  above or below it. When the window, or its side in the window of `hok`, is too narrow for
  them, the marks, the kinds and the buttons under the rows go on two lines, and the search goes
  under the kinds, at full width (Seb, 2026-10-20: "agent list should not have a global
  horizontal scrollbar that hides the search when too small, make it responsive"): the side of
  `hok` scrolls sideways only once it is half as wide as before. Under the rows, behind a handle, the queue of `bctl queue`, read every 10 seconds, as `GET /queue`
  runs `podman ps`, in its order, which no header click changes. A task there has its own state,
  paused, over the cap or else queued (Seb, 2026-10-21, as the mark of its bundle showed a task
  behind a running agent as running), and the kind of its bundle (Seb, 2026-10-19), that of its
  row when it has one, else the one its window would show once the session it goes on with is
  idle; its last message, its own text or,
  for a session queued again as it was, the last prompt of that session; and its last answer,
  the close line of the last answer of that session, else its first line. A new task has no
  session yet, so no answer. Under it, Force lets each
  selected task start over the cap (`POST /force-queue` with its bundle), Up and Down move the
  first selected task one place (`POST /move`), and Pause, Resume and Time set when each selected
  task may start (`POST /schedule`): once resumed, once a slot is free, or at the time of the day
  Time asks, its own by default (Seb, 2026-10-16: "controls on when it goes out of the queue").
  A right click on a task opens a menu with these six (Seb, 2026-10-20).
  Ctrl+click and Shift+click select several rows:
  Enter, a double click or Terminal brings up their terminals where they were, the first row in
  front (his pick: no tiling); VS Code opens the window of each selected bundle; Show tabs brings
  up the Firefox tab group of the first selected bundle, and Close tabs closes the groups of each,
  at once (Seb, 2026-10-20: a closed tab comes back with Ctrl+Shift+T); Close closes the
  window of each after a question, as the daemon does (SIGUSR1 to its `agent-terminator.py`), so
  its claude stops and its tab groups close. Back to queue, after a question too, queues the
  session of each selected bundle again (`POST /requeue`, at the lowest priority of the queue, so last), and the daemon
  closes its window once its turn ended, 2 minutes at most: when its turn comes, a new terminal
  goes on with that session, told that its window closed and to check what changed since its last
  turn (`RESUMED` in `daemon.py`). Pause does the same with
  `paused`: the task waits in the queue until Resume or
  Time there (Seb, 2026-10-16: "move agents from and to the queue easily, or pause them"). A
  right click on a row opens a menu with these seven. New ops agent runs `bctl
  open ops --terminal`. It runs on the system python of the host, which has GTK. It is dark when the
  `color-scheme` of the desktop portal asks for it, as VS Code and the browsers are, and follows
  each change of it (Seb, 2026-10-16: "should follow os color theme"). Dark is the dark Adwaita
  of GTK, as the GTK theme the desktop names may have no dark variant. A second start brings the
  open window up, and its size, its place and the height of the queue are kept in
  `~/.local/state/bundle-ctl/agent-list.json`. Its title says how many agents wait for Seb,
  failed, finished or waiting, `Agent list (3)`, for the window list of the panel, and its icon is
  `agent-list.svg` (Seb, 2026-10-19: "the counter of pending ... as well as the same for agent
  list", "a specific icon for each app").
  These parts are `Agents`, which the window of `hok` shows too (Seb, 2026-10-20: "merge agent
  list and hok list windows, with clear relations between all items"): when host-ctl is on the
  machine, a start runs its `hokwin.py`, which loads `agent-list.py` and puts each request under
  the row of the agent that asked, the others in groups after the agents (the host-ctl README
  has the rest). This window opens alone when `hokwin.py` is missing or ends with an error within
  3 seconds. Its owner, the window that shows `Agents`, gives the rows under each agent from its
  key and its session (a `Child` each, read with the rows: the `.agent/session` of a bundle, the
  session an ops window is named after), hears which of its rows is selected, and sets the
  title. A row of the owner has no window: the buttons and the menu leave it out. The rows hide
  their expanders while none has rows under it. In the window of `hok`, a `🔑 hok` button before
  the marks keeps the agents with a request under them, then the groups, all opened, and
  counts those requests among the rows the kinds and the search keep (Seb, 2026-10-20: "I
  lack a way to easily see what is waiting for hok like before"). It adds to the marks as one
  more of them: `🔑 hok` and `✋ waiting` keep the agents of either, and a mark pressed without
  it hides the groups (Seb, 2026-10-21: "I want all hok and all waiting, not the
  intersection"). The kinds pressed cut it as they cut the marks: they keep the groups of their
  kind only, so not those of the host or of a gateway, and its number counts those (Seb,
  2026-10-22: "hok filter should combine like other state filters but respect type filters").
  The agent list alone does not show it.
- `window-applet/`, a Cinnamon applet, shows the mark that needs Seb most and how many bundles
  have it, in the order ❌ ✋ ✅ 🚀 ⏳ (Seb, 2026-10-15: a failed agent is an error to fix, a
  waiting or finished one waits for him, one stuck on starting comes after, one in progress does
  not), or 🪟 when no bundle has a mark. Its
  tooltip has the counts of each mark, and a click opens `agent-list.py`, or brings it up. The
  rofi list stays on its shortcut.
  The label comes from `window-picker.py --counts` every 2 seconds. It also sets `skip_taskbar` on
  an agent window (its terminator runs on a `bundle-ctl/terminator-` config) while it is
  minimized: as it is minimized, and as it is created, since it opens minimized. It is a link to
  that folder, `~/.local/share/cinnamon/applets/bundle-windows@useful-things`, added to a panel in
  the `enabled-applets` key of `org.cinnamon`.

`python3 test_window_picker.py` and `python3 test_agent_list.py` run each case.

`agent-terminal.py BUNDLE` started on a bundle whose run already began (`run.lock`) does not run
the task again: it opens the session as it was, in its permission mode (`.agent/mode`) and with
no prompt. When another terminal holds the session (the pid in `.agent/terminal` lives), it first
takes the session over as a window does, with `client/agent-takeover.py` in the container that
terminal recorded: that terminal closes, the container is kept, and a turn cut at a tool call
goes on (`Continue from where you left off.`). It then removes `handoff` and `restarted`, which
are for a tab. This is how an agent moves from one terminal to another without a new container
or a new turn.

A VS Code window opened on the bundle (the folder icon of `gbs`, `ocode`) attaches to the same
container and takes the session over, before or after its verdict: claude-autoopen sees
`.agent/terminal` and runs `client/agent-takeover.py`. It stops the terminal's claude at once when
a tool call is in flight or the session is idle, else at the first of the two, and writes
`handoff`. A turn that ended on a background command (`shell`, the PR watch of a review) counts as
idle: that command ends with the terminal's claude. A session with no record counts as idle once its last turn ended 2 minutes ago: two
containers whose claude has the same pid write the same `sessions/<pid>.json`. `python3
test_takeover.py` runs each case. The terminal window closes, and from there on the bundle has an
agent window (below): the tab opens on the session and, for a stopped tool call, reruns the turn
after one reload. The container then lives as long as that window.

The way back is the terminal icon of the Claude tab ("Move the agent to a terminal", shown in a
window whose bundle has an agent session), at once, mid-turn too. The claude of the tab cannot
move to another process, so the window closes and the terminal starts the container again:

- claude-autoopen refuses while another Claude session of the window works, which the closing
  would cut, and else runs `bctl open BUNDLE --terminal`. The daemon takes that from the bundle
  itself or the host only, as it closes a window.
- `agent-terminal.py` writes `.agent/terminal`, then `.agent/close-window`, on which the window
  closes at its next poll, and waits for the Dev Containers shutdown monitor to stop the container
  (10 s). A window still open after 2 minutes (a save prompt answered "Cancel") keeps its session,
  and the terminal says so.
- It then starts the container, and resumes the session in the mode of the tab (the last
  `permissionMode` of the transcript), with `Continue from where you left off.` when the turn was
  cut: a tool call in flight, a test run say, runs again. About 20 s in all.
- The terminal window opens minimized, as any agent window: the agent goes back to the window
  list.

`devcontainer/claude-config.py` marks the onboarding as done in the containers' Claude config: the
interactive claude stops on its theme picker otherwise.

## PR events

The agent of a terminal learns at once when someone else acts on a PR of its bundle (Seb,
2026-10-21: "new review comments, someone else pushed, etc [...] no polling though"), and decides
what to do: ask Seb to validate, or act.

- mail-watch of mail-agent names to the mail gateway the PRs of each new GitHub mail, about 15 s
  after it lands. `client/prwatch.py` waits there (`GET /_gateway/touched?after=N`, answered at
  the next mail or after 50 s) for the PRs of the bundle: those whose head is the branch of the
  bundle, in any repo of `/workspace` on that branch, found when it starts. `agent-run.sh
  --terminal` starts it beside claude, in a session of its own, once per container (a lock).
- For one of them it reads on GitHub what came since its last look: reviews, review comments and
  comments of anyone but Seb, a head the bundle does not have in its branch, a close. It leaves one
  line in `.agent/pr-events/` that names them, with no text a stranger wrote (the agent reads them
  with gh), and what to do with them (`ADVICE`).
- The first look of a PR starts from the head GitHub has then, so a commit amended and not pushed
  yet is not told as a push of someone else. `.agent/prwatch.json` keeps the last look of each PR.
- Each line goes to the agent once, by the first of three: a hook of the bundles' Claude settings
  at its next tool call or prompt (`prwatch.py --hook`, PostToolUse and UserPromptSubmit),
  `client/agent-stop.py`, which keeps the turn going rather than let it end, and
  `agent-terminal.py`, which types the line, then Enter, as the next prompt of an idle session.
- Idle is the `idle` status of the session in the records of claude: no turn, no dialog open
  (a question or a permission is `waiting`), and no wait for the reset of the usage limit. Only
  the record of the claude the terminal started counts: one stopped with its container leaves its
  own, `idle`, and a line typed before claude runs (agent-run.sh waits 10 s first) reaches it with
  its Enter as a paste, left in the prompt (2026-10-23). The
  terminal types nothing while a key Seb typed since his last Enter may be a draft (a mouse move
  or a focus change is none), nor after a slash command he sent, which may leave a menu open: his
  next prompt then carries the line, by the hook.
- What Seb posts himself from Firefox (a review, a comment, an r+), which prwatch.py leaves aside,
  comes the same way (Seb, 2026-10-22: "currently I have to tell it manually"): the helper of
  firefox-gateway runs `pr-posted.py BUNDLE` with one line on stdin that names it, and the script
  leaves it in `.agent/pr-events/` of the bundle, with an advice of its own (go on from it, no
  confirmation asked of him).
- Not a Claude Code channel, though it would wake an idle session too: a seat of a Claude Team
  organization gets none until an admin of the organization turns channels on, and Seb wants no
  such ask nor a feature Claude turns on remotely (2026-10-21).

`python3 test_prwatch.py` runs the looks on a fake GitHub and a real git repo, the wait on a fake
gateway, the hooks, the Stop hook and pr-posted.py; `test_terminal.py` the typing.

## Ops agents

A Claude session of the ops container (`BUNDLE_CTL_HOST_FOLDER`) can run in an agent window of
its own too, as a tab of the ops VS Code window has no mark and no row in the window list (Seb,
2026-10-14: "so I can handle it similarly"). `bctl open ops --terminal` opens one, waiting for a
first prompt, and `bctl open ops --task TEXT` one on that task. `bctl open ops` alone opens the
ops VS Code window. Only the host and the ops container may ask: the task of a bundle would run
where `hctl` is. A bundle sends a task alone, as feedback on the tooling (a skill, a hook, bctl, a
gateway): the daemon holds it as `bN`, `hok` shows it word for word, and a yes opens an ops agent
on it, after a line that names the bundle. `bctl open ops --task TEXT --wait 1750` waits for that
answer as `bctl delete` does, and the same task sent again takes back the request that waits.

- The window is named `ops-SESSION`, a new session id, where a bundle window has the bundle name:
  the terminator config, `agent-terminal.py ops-SESSION`, `BUNDLE_CTL_AGENT_WINDOW`. The task
  waits in `~/.local/state/bundle-ctl/ops/SESSION/`, removed when the window closes.
- `agent-terminal.py` runs `claude --session-id SESSION --model opus --permission-mode auto` in
  the ops container, in the repos, started first when it is not up. Other sessions work in that
  container: quitting claude or closing the window never stops it, and a closed window stops its
  own claude (`pkill`).
- No cap and no queue: the window opens at once, as a tab would. Opened with a task it opens
  minimized, as any agent window; with no task it shows in front, for the first prompt.
- The title shows the title of the conversation in place of a name, with 🔧 and `(ops)`:
  `⏳ 🔧 Ops list layout (ops)`. The marks are those of a bundle, the close line first (Seb,
  2026-10-16), but for an idle session whose last answer has none: ✅ when that answer has a Done
  part and neither a Decide nor a Your action part, the parts of Seb's reports, ✋ otherwise (Seb,
  2026-10-14). The ops container has a sessions folder of its own, as
  a bundle container has: `agent-terminator.py` looks there by session id. It reads the title
  again every 10 seconds, and drops the mark once its claude ended.
- `window-picker.py` lists the minimized ops windows with the bundles, by mark, and a click brings
  one up.
  Its first row, `➕ new ops agent` (Seb, 2026-10-14), runs `bctl open ops --terminal`. The applet
  counts them with the bundles, and hides them from the task bar when minimized.
- A session of a tab of the ops VS Code window moves to an ops agent window (Seb, 2026-10-14: the
  icon of the bundles): an extension of that window stops the claude of the tab, closes the tab,
  and asks `bctl open ops-SESSION --terminal`. The daemon takes it from the host or the ops
  container, on a session that has a transcript and no `agent-terminal.py` running it. The window
  resumes the session in the mode of the tab, with `Continue from where you left off.` when the tab
  cut a turn, and opens minimized, as any agent window that takes a session.
- The way back is the `orepo` link that a pane of the window split by hand prints,
  `odoo-bundle://ops-SESSION` (Seb, 2026-10-14: "should automatically close the existing terminal
  on that agent"): `bctl open ops-SESSION`. The daemon closes the window (SIGUSR1 to its
  `agent-terminator.py`), waits up to 30 s for its claude to end, writes the mode of the session and `ops-window-open/SESSION` in the
  storage of the Claude extension of the ops container, and opens the ops window. The ops-window
  extension of host-ctl opens the tab within 2 s, with `Continue from where you left off.` in its
  input when the window cut a turn. Opening the ops window by hand moves no session: the ops agent
  windows share its container.
- A tab of the ops window that resumes the session from its list closes the window within a second
  (a `claude-vscode` record of the session). The window stops only its own claude, the one `podman
  exec` started (parent pid 0).

A window closed ends its session: the conversation goes on from the `/resume` list of a tab, in a
tab with `bctl open ops-SESSION`, or in a window again with `bctl open ops-SESSION --terminal`.

## Cut sessions

A crash of the X server, a reboot or a restart of the ops container kills every claude at once, in
the middle of its turn: Seb asked 22 times to restart such sessions, and typed "I restarted ops,
continue" in 13 of them on 2026-10-09. `bctl open --cut` lists them, and `bctl open --cut --resume`
asks Seb to resume them all behind one yes (Seb, 2026-10-16). Only the host and the ops container
may ask: the list shows the prompts of every bundle.

A session is cut (`cut.py`) when:

- no claude runs it: no claude process of the host names it on its command line (`--resume`,
  `--session-id`), and no record of a live claude names it in the sessions folder of a container,
  whose pid and start are matched with the last `NSpid` and the start time of the processes of
  the host. For a bundle, its container is down too;
- its transcript was written in the last 3 days;
- its last turn had not ended: its last message is a tool call with no result, or a tool result
  or a prompt with no answer, or the turn ended on a short answer that only says where the work
  is, while a background command or agent it started still ran. An interruption by Seb (Esc), a
  command of his (`/model`), the summary of a compaction while idle, a report, a verdict and a
  close line ("Nothing left", "Waiting for you") end a turn. A `claude -p` run is never cut.

A bundle counts with its newest session only, found by the title its agent gives it (the bundle
name) or by `.agent/session`: an older session cut there was followed by other work. A bundle whose
folder is gone is left out. Every transcript of the project folder of `REPO_ROOT` is an ops
session: a session of the host in `~/repo` too, which would go on in the ops container.
A `stopped` agent whose last turn ended is not cut: on 2026-10-16 the 5 of the last 3 days had all
ended on a report Seb had read, after a chat of his ("ready thanks", "send to ops").

`bctl open --cut` prints for each its bundle or `ops-SESSION`, the title of the conversation, when
it was cut and why, its last call and its last prompt, cut to 160 characters. With `--resume` the
daemon holds one request, `bN`, that `hok` shows as "resume N sessions a crash or a restart cut",
with these lines and the prompt of the first one. The same request asked again takes back the one
that waits, and `--wait S` waits for his answer as for `delete`. His yes, for each session still
cut:

- an ops session goes on at once in an ops agent window, its prompt as the `task.md` that
  `agent-terminal.py ops-SESSION` passes to `claude --resume`;
- a bundle session is queued with `resume`, the session, and the priority, the model and the
  effort of the last task of its agent: it goes on in its agent terminal when the queue lets it,
  and the last run of its agent goes to `.agent/history/`;
- a session that runs again or whose turn ended since, a bundle already queued or open in a
  window, is left as it is, and the answer in `hok` says why.

Before that, the agent window where the claude of the session ended, on "Press Enter to close",
closes as the daemon closes a window (SIGUSR1 to its `agent-terminator.py`), and the daemon waits
up to 30 s for that process to end; a window that stays open leaves the session as it is. A
window with an `agent-terminal.py` still running is not touched. On 2026-10-20 a rebuild of the
ops container left 6 ops windows on "ops: claude ended with code 137", and Seb had to say yes to a
`kill` of them before the yes to the resume (Seb, 2026-10-20: one card). The wait matters for an
ops session: its window removes `~/.local/state/bundle-ctl/ops/SESSION/` as it ends, and would
remove the `task.md` of the resume, or the files of the new window. A bundle whose container
stopped under its agent leaves such a window too, closed the same way before its resume is queued.

The prompt starts on `Continue from where you left off.`, says when the session was cut, that its
claude stopped with its container or its window and nothing it did, that its background commands,
servers and agents died with it, and to check what its last call already did (a commit, a push, a
file written, a request held in hok) before running it again; then that last call. The restarts of
2026-10-08 told one container the same. `python3 test_cut.py` runs each case on fake transcripts
and a fake `/proc`, the request held for `hok`, the client, and the yes, with the windows it
closes first.

## Agent windows

A task given to a bundle whose VS Code window is open runs in that window. The Claude tab cannot start
work by itself (`claude-vscode.editor.open` only fills its input), but a tab revived by a window
reload reruns the interrupted turn of its session (`claudeCode.continueAfterReload`). So
claude-autoopen starts the task with `claude -p` in a terminal named "agent", which stops it at its
first tool call, opens the tab on that session, and reloads the window once: the tab then runs the
task live. An extension host restart would revive the tab too, but in a dev container it loses the
remote connection ("Cannot reconnect"). A run that ends without a tool call opens the tab on the finished session.

| file in `.agent/` | written by | meaning |
| --- | --- | --- |
| `task.md` | bundle-ctl | the prompt |
| `model` | bundle-ctl | the model the task asked for, `haiku` or `sonnet`; no file for `opus` |
| `effort` | bundle-ctl | the effort the task asked for, `low`, `high` or `xhigh`; no file for `medium` |
| `queued` | bundle-ctl | when the task entered the queue (epoch seconds), kept when a task no window took is queued again |
| `terminal` | `agent-terminal.py` | the agent runs in a terminal of the host; removed when it ends, or by `client/agent-takeover.py` when a window takes the session over |
| `container` | `agent-terminal.py` | the id of the container the terminal's agent runs in, for the terminal that takes its session over; removed when it ends |
| `session` | `agent-terminal.py`, claude-autoopen | the session id, chosen before the run |
| `run.lock` | `client/agent-run.sh` | the run started, its mtime is when; a terminal started again opens the session and does not run the task again |
| `result.md` | `client/stream-format.py`, `client/agent-stop.py`, `agent-terminal.py` | the last answer of the run, or why no container could start |
| `mode` | `client/agent-run.sh`, `client/stream-format.py`, `client/agent-takeover.py` | the permission mode of the run, which the tab resumes the session in; `auto` from the start of a terminal, so the tab does not fall back to plan after its claude ended |
| `handoff` | `client/agent-run.sh`, `client/agent-takeover.py` | claude was stopped for the tab to go on: `claude -p` at its first tool call, or the claude of a terminal |
| `retry` | `client/agent-run.sh` | `auth failed, retry n/3`: the run failed to authenticate and starts again |
| `waiting` | `client/agent-stop.py` | a turn of the terminal or of the tab ended without a verdict; its mtime is when |
| `done` | `client/agent-run.sh`, `client/agent-stop.py`, `agent-terminal.py` | the exit code of `claude -p`, or `interrupted`; `0` when a turn of the terminal or of the tab ends on a verdict; `failed` when no container could start |
| `tab-opened` | claude-autoopen | the tab was opened on the session once; removed by `agent-terminal.py` when it takes the session from the window, so that a later window opens its tab again |
| `close-window` | `agent-terminal.py` | the terminal takes the session from the window: the window closes; removed when the wait ends, and ignored after 2 minutes |
| `restarted` | claude-autoopen, `client/agent-takeover.py` | the window was reloaded once for the handoff, or needs no reload (the session was idle when taken over) |

The reload waits while a Claude session of the container is busy or waits for a permission. If the
tab does not rerun the turn (a claude-code change), the session waits there with its prompt
unanswered: "continue" resumes it. `client/agent-stop.py`, a Stop hook of the bundles' Claude
settings (`/home/seb/src/odoo/.claude/settings.json`), writes `result.md` when a turn of the tab
ends, and `done` when its first line is a verdict: `pushed:`, `ready to push`, `needs Seb:`,
`blocked:` or `nothing to do:`. A turn that ends on anything else (a background job still running) leaves
`waiting`. Ctrl+C in the "agent" terminal before the first tool call stops the run, and the tab
opens on its session. The "agent" terminal closes when the tab opens, unless `claude -p` failed: its
log is then the only trace of the error. The tab opens in place of a pinned Claude tab of the window, unless a Claude
session of the container is busy or waits for a permission.

`bctl create|fetch|open ... --task-file FILE` queues the task, and the daemon starts agents from
the queue while fewer than `BUNDLE_CTL_MAX_AGENTS` (default 8) agents are at work in a terminal
(an agent that gave its verdict no longer counts), plus the ones launched in the last 15 minutes
whose container is not up yet and that did not fail to start, and while the host is not short of
memory (the task then waits for `memory on the host`) nor its CPU busy (`the CPU of the host`). A bundle open in a VS Code window does not count, a session a window took over included: the cap
bounds what the agents spend, and Seb's windows are his.
`--model haiku|sonnet|opus` (default `opus`) is the model of the task's agent, for the one that
queues to send a job that needs less to a model that costs less. `--effort low|medium|high|xhigh`
(default `medium`) is how much that agent thinks: `medium` is where Anthropic's guide starts on
the three models, and the `xhigh` of Seb's settings stays for his own sessions. The daemon refuses
any other name, `max` included, and `client/agent-run.sh` runs on `opus` at `medium` when
`.agent/model` or `.agent/effort` holds anything else. `python3 test_options.py` runs each case.
`--priority N` (-100 to 100) puts the task ahead of the ones with a lower priority; equal
priorities leave in arrival order. With none, a task from the host gets 0, and one an agent queues
the priority of the agent's own task, kept in `.agent/priority` (Seb, 2026-10-15: "same level as
his, unless it thinks it should be higher"). `--after HH:MM` keeps the task in the queue until that
time of the host comes, today or tomorrow, whatever its priority: the queue says it waits for `its
time, HH:MM`, and the bundle of a review, queued, is not deleted meanwhile (Seb, 2026-10-15: close
the review agents that wait for him in the evening, and have them check what came on their PR the
next morning). `--paused` keeps it there until it is scheduled again: it waits for `a manual
resume`, before anything else, and its bundle is kept too. That is how agents are parked for a
reboot: on 2026-10-16 an emptied queue let the daemon delete 12 review bundles. When the task is
the session of the agent, a review bundle loses its checkout all the same, and the task keeps what
brings it back (see Disk). `bctl schedule
BUNDLE spot|manual|HH:MM` changes when a queued task may start: once a slot is free, once
scheduled again, or at that time; a forced task no longer goes over the cap. `bctl force-queue
BUNDLE` starts it at once, its time or its pause aside; a force of the whole queue leaves both.
`bctl requeue BUNDLE [--paused]`, Back to queue and Pause in the agent list, puts a running agent
back in the queue, last, and waits for the end of its turn to close its window, 2 minutes at most
(Seb, 2026-10-16: "ok for end of turn but with a small timeout, I don't want to wait 20 minutes
for a test"). Meanwhile `.agent/pause` holds that time, and `client/run-hook.py` refuses each Bash
call on a reason that says when the window closes and asks for the end of the turn, on where the
work is (his "can we prompt the agent to finish quickly"). A turn still running at the end, a test
run for one, is cut: its task becomes the prompt of a cut session, which says that Seb stopped it
and what to check before its last call runs again. The agent is not asked to save its work: its
files and its transcript stay as they are, and a resume reads both.
`bctl requeue BUNDLE --github` is the same, asked by the agent of BUNDLE itself when its work
cannot go on without GitHub: git-gate tells it to when the GitHub connection of the host is down.
That connection comes back only on Seb's yes in Bitwarden: on 2026-10-19 it was down from 18:45
to the next morning, and 13 agents ended "blocked" with their work ready and not pushed, until
someone resumed them (Seb, 2026-10-20: "ok for resume"). The task waits for `the GitHub
connection of the host`, its socket in `~/.local/state/ssh-github-mux/sock/`, and meanwhile the
daemon asks for the connection every 10 minutes (`request/start`), so that Bitwarden asks Seb
again. Once it is up, the session goes on, told that it is back. `bctl schedule BUNDLE spot` and
`bctl force-queue BUNDLE` let it go before; a force of the whole queue leaves it.
The answer is the task's place (`queued`) and what it waits for
(`waits_for`), or `started` when a slot was free. `bctl status` lists the queue in that order with
each priority, the bundle that queued it, since when and what it waits for, so a caller can pick
where it goes.
A task for a bundle whose agent has not ended is queued behind it, so the caller has nothing to
try again: it waits for `the previous agent of the bundle to end (at work since HH:MM)`, the
start of its run (Seb, 2026-10-21, as `its agent to end` read as the task waiting on itself:
"why would a queued item wait on itself?"). Once an agent in a terminal gave its verdict, that
terminal types the task as the next prompt of the same session (see below). A
VS Code window opened on the bundle meanwhile first
takes that terminal's session over, and the task starts in the window at the next pass. A bundle
holds one queued task: a second one is refused.
One agent starts at a time, as several containers and claude sessions that start together take
the memory of the host at once: the next task waits for `the agent of BUNDLE to start`, until the
run of that agent began (its `.agent/run.lock`), its launch failed, or 15 minutes passed.
`bctl force-queue` lets the tasks queued now start over the cap and while the host is short of
memory, still one at a time and each
after what else it waits for; `bctl status` shows them `over the cap`, and a task queued later
waits for a slot again. Only a process of the host may ask it: a container gets a 403, the one
of `BUNDLE_CTL_HOST_FOLDER` too, so that going over the cap stays Seb's call. `bctl queue` lists
the queue alone, at once, and ends on the memory of the host: `the host is short of memory` when
one more agent would not fit, `the host CPU is busy` when its CPU holds the next agent (see
Memory).
The cap is read from `~/.config/odoo-dev/config.env` or the unit's environment. The queue is checked
again on every podman container start and stop, every minute, and every 10 seconds while a task
waits for an agent to start; it lives in
`~/.local/state/bundle-ctl/state.json`. Any session can queue, agent runs included: the cap
is the only bound on a fan-out. A task given to a bundle whose window is open starts there within 5
seconds, even at the cap, as it takes no new container; behind an agent of that window, within a
minute of its verdict. A launched task nothing took (no
`.agent/session` once the launch is 15 minutes old and no container is up) goes back at the head
of the queue, 3 times at most: a terminal asked for while the desktop session ends never starts.
A task queued behind it meanwhile replaces it, as it replaces a stopped agent.
A queued task whose bundle folder is gone is dropped, unless it holds the script that makes the
bundle, and so is one its agent already runs (a pass
that dies after a launch saves no state): `python3 test_queue.py` runs a pass on each case, and
real clients that read and force the queue.
A new task moves the previous
`.agent/` files of the bundle into `.agent/history/`, unless it is the same task relaunched before
any run started. `bctl open BUNDLE --resume` leaves `session` in place: the terminal goes on with
that session (`agent-run.sh` resumes a session that has a transcript), and the task is its next
prompt. When none is given, `Continue from where you left off.` and, at the launch, that its window
closed and what to check since its last turn. This is how an agent cut before its
verdict (`stopped`) starts again with what it knew. It is refused when the bundle has no session,
or is open in a window, where the session goes on in its Claude tab.
Closing the window of an agent is Seb's call, and so is closing its terminal before its verdict
(see Agents in a terminal); nothing deletes a bundle without his yes. `done` says the run reached
its verdict: Seb may still go on in the session, in its terminal while it is on screen, or with
`bctl open BUNDLE --terminal` once it closed. A task for a bundle whose last
session still has its terminal goes to that terminal once that session gave its verdict.

There it is the next prompt of the same conversation, typed by the terminal, whether its window is
minimized or on screen; no window opens or closes (Seb, 2026-10-21: "ideally it should auto fix",
picked over a new terminal for a minimized window and a card in `hok` that asked him to close one
on screen). The daemon moves the files of the last run to `.agent/history/` as for any new task,
but leaves the session, the files of the terminal and of its PR watch, and the model and the
effort of the session, which claude keeps: a task queued on another model runs on the one of the
session. `agent-terminal.py` then types the task as one paste, then Enter, as it types a PR event
(see PR events): only in an idle session, never over a draft of Seb nor after a slash command he
sent, and before a PR event that waits. It makes `.agent/run.lock` as it types, which begins the
run; until then `client/agent-stop.py` writes no verdict, as a turn that ends then is not the
task's. Until the session is `idle` (no turn, no dialog, no background command a turn left, no
wait for the reset of the usage limit), the task waits for `the previous agent of the bundle to be
idle`; a task with a time waits for it, and one that names another session waits for `the
terminal of the previous agent of the bundle to close`. The
journal says `BUNDLE: the task from PARENT goes to its terminal`. `python3 test_queue.py` and
`python3 test_terminal.py` have the cases.

The agent runs of all the containers start at least 10 seconds apart (a lock in the shared
`~/.claude`), as claude processes started together fail each other's OAuth token refresh. A
`claude -p` run that still fails with `Failed to refresh OAuth token` or `Failed to authenticate`
before its first tool call starts again 1 to 2 minutes later with a new session, at most 3 times.
In a terminal the failure shows on screen, and nothing retries it.
