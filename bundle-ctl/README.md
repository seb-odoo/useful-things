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

See the header of `bundle-ctl.service`. It runs with the odoo20 venv, which the scripts need.

## Verbs

| verb | answer |
| --- | --- |
| `GET /whoami` | the caller's bundle, base and parent |
| `GET /branches` | `gbs --json` run on the host, which deletes the bundles left with merged PRs only; `?format=table&width=N`: its colored table |
| `GET /status` | every local bundle branch: base, behind and conflict per repo, folder, opening (launched in the last 15 minutes, container not up yet), open, and its agent: state (`running`, `waiting`, `done`, or `stopped` when its terminal or its window closed before the verdict: nothing runs it any more, and a new task may replace it; a stopped agent carries its `task` text, to queue it again as it was), when it was queued, when its run started (none for a task no window took), when it ended, `stale` when a branch of the bundle got a commit, or its dev remote ref an update (the mtime of its reflog), after that end, retry, and the last write to a session titled with the bundle, its own or an earlier agent's tab Seb went on in (`active N min ago`, or `idle since HH:MM` after 15 minutes; a tab opened by hand has no title and does not count); the queue, each task with its priority, the bundle that queued it, when, and what it waits for |
| `POST /add-repo` | `add_repo.py`: the worktree of a repo the bundle was made without (`design-themes`, `documentation`); a container asks for its own only |
| `POST /create` | `gnb` with `--no-push`, then a window, or with a task its agent; refused when the bundle exists, a branch has unpushed commits or the disk is short (see Disk) |
| `POST /delete` | the deletion of a bundle, held for Seb's yes in `hok` (see Deletion); a container asks for its own only |
| `GET /held` | what became of a deletion this caller asked (`?id=bN`): it waits, or what Seb said in `hok`; `?wait=S` long-polls it, 50 s at most |
| `POST /fetch` | `pfb` on a bundle name, a PR link or a fork label, then a window, or with a task its agent; refused when the bundle is here, a branch has unpushed commits or the disk is short |
| `POST /open` | a VS Code window on a bundle folder, or with a task its agent, in the bundle's last session with `resume`, or with `terminal` the agent terminal on that session and no prompt, taken from its VS Code window when the bundle itself or the host asks; refused on a half-made bundle |
| `GET /job` | the state and log of a create/fetch/open, long-polled by `bctl` |
| `GET /queue` | the queue of `GET /status` alone, at once: the agents at work in a terminal, the cap, each task with what it waits for, and the memory left on the host, `short` when a start would add to a host that already lacks it |
| `POST /force-queue` | the tasks queued now may start over the cap; then the answer of `GET /queue`, with the tasks it forced and the ones that started. Refused to a container |
| `POST /run` | a heavy command waits for cores: one JSON line each time its place changes, then the CPUs it got (see Runs) |
| `GET /runs` | the runs that hold cores and the ones that wait, the pool and the memory available |

A `create` or a `fetch` that fails leaves a half-made bundle: a folder with no `.devcontainer`,
which the scripts link last, so no container can start in it. The same `create` or `fetch` sent
again finishes it.

`add-repo`, `create`, `fetch` and `open` answer 202 with a job id, and the jobs run one at a time, as two
fetches of one repo collide on its remote refs. A job takes the `DISPLAY` and `SSH_AUTH_SOCK` of the
desktop session from `systemctl --user show-environment`, so it fails until Seb is logged in. The
daemon never pushes.

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
  `BUNDLE_CTL_RUN_MIN_FREE_GB` (default 4) of available memory, the runs started in the last
  minute counted at what the table says, or when the host stalls on memory (`full avg10` of
  `/proc/pressure/memory` at 5 or more). The first run always starts.
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

## Disk

A bundle takes about 3 GB at once, so agents that each make one can fill the disk in minutes. The
daemon reads what is left on the disk of the bundles:

- Under `BUNDLE_CTL_DISK_STOP_GB` (default 30), `create` and `fetch` are refused, and the queue
  holds its tasks, forced ones too: `waits for disk space (20 GB left, 30 needed)`. They start by
  themselves once the space is back.
- Under `BUNDLE_CTL_DISK_WARN_GB` (default 60), a desktop notification says what is left, then
  again at each 10 GB less, and again once the disk went 10 GB over that line in between.
- `python3 test_queue.py` has the cases.

A bundle of someone else's PR (no `BUNDLE_SUFFIX` in its name) goes by itself once nobody used it
for `BUNDLE_CTL_REVIEW_IDLE_DAYS` (default 2, Seb's choice on 2026-10-13): no window open, no task
queued, and no file newer than that among its `.made`, its `.agent/` files, its Claude config
(written at each window open and Claude start) and the reflog of its worktrees. The daemon looks
once an hour and runs `delete_bundle.py`, which keeps a bundle with uncommitted files or a commit no
remote has; that one is tried again only after a new use. `bctl fetch` brings a bundle back.
`python3 test_delete.py` has the cases.

## Wrong version

`client/check-base.sh` is a SessionStart hook of the shared bundle `.claude/settings.json`: when
the open PR of the bundle targets another base, it tells the session to `bctl create` a bundle on
that base with a task to cherry-pick the commits. The agent of the new window does the
cherry-pick, as all the bundles share the base repos' `.git`.

## Deletion

`bctl delete [BUNDLE] --reason TEXT` lets an agent whose work moved to another bundle ask for its
own to go, where it would hand Seb a `delete_bundle` line to type. It is refused while a branch of
the bundle holds work: a commit of Seb no remote has, a branch on the dev remote that no PR uses
yet, or an open PR. A branch whose PRs are all closed or merged goes, even with commits of others
no remote has: in a review bundle, the older head of a PR its author rebased since.

- The daemon holds the request in `held/` beside `state.json`, in the format of
  `gateway-core/held.py`, and `hok` lists it as `bN` (the `config.py` of host-ctl names the
  folder). No container mounts that folder: who asks cannot answer. Without gateway-core beside
  useful-things, the verb is refused.
- A yes checks the branches again, then runs `delete_bundle.py BUNDLE` as a job, at once when the
  container of the bundle stopped, else when it stops: `deletions` in `state.json` holds the
  bundles that wait for that. Closing the window or the terminal stays Seb's call.
- `--wait S` waits that long for his answer and prints it, as `hctl --wait` does: exit 0 on his
  yes, 126 when he refused, 75 when it still waits. The same delete asked again replaces the
  request that waits, under the same `bN`, and waits for it.
- `delete_bundle` still refuses uncommitted files: the job log in `jobs/` says so. Commits of
  others need its `--force`, which drops such files too: the request is then refused on them.
- `python3 test_delete.py` runs each case on git repos of its own.

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
- A turn that ends on a verdict writes `done` (`client/agent-stop.py`), a turn that ends on
  anything else `waiting`. Either way the claude session stays open in its terminal, for Seb to
  read what ran and to type in. A `done` agent leaves its slot of the cap, a `waiting` one keeps
  it. Both keep their container, so a new task waits for `memory on the host` when the host is
  short of it.
- Quitting claude or closing the terminal stops the container, and nothing else does: Seb may
  type in that session at any time. Without a verdict the agent then reads `stopped`. So does a
  terminal that dies without closing (killed, or lost with the X server): `podman exec` does not see that hangup, so the wrapper ends on the hangup and stops
  the container itself. With a VS Code window attached, it keeps the container and hands the
  session to that window instead (`client/agent-takeover.py`, as the window does when it opens):
  else its claude runs on with no terminal, and the tab finds the session open in another process.
- `bctl open BUNDLE --terminal` opens that terminal again, on the bundle's last session and with
  no prompt: the way back once the X server took the windows away. It is refused while a terminal
  holds the session: a new terminal would take it over and cut its turn. A VS Code window that
  holds it gives it back (below).
- A claude that ends on an error leaves the terminal open on its exit code and on the tool calls
  and answers of the session (`client/stream-format.py` on its transcript): claude draws on the
  alternate screen, which empties when claude ends.
- When no container could start (a failed `up`), the terminal stays open on the reason, and the
  wrapper writes it to `result.md` with `done` as `failed`: `bctl status` shows it to the bundle
  that queued the task, and the launch gives its slot back at the next check of the queue.

With `AGENT_TERMINAL` set to `agent-terminator.py {bundle}`, the window is a terminator split in
two: the agent on the left, and on the right `agent-terminal.py BUNDLE --shell`, a shell of the
host in the `odoo` folder of the bundle. That shell is for Seb to type in, so nothing of it runs
in the container: the contained terminal is the one of the VS Code window.

- A pane split by hand (Ctrl+Shift+O, Ctrl+Shift+E) and a new tab (Ctrl+Shift+T) are shells of
  the host too, as in any terminator window.
- Terminator takes the commands of a split from a layout of its config only. So the launcher runs
  it on a copy of `~/.config/terminator/config` with that layout added, in
  `$XDG_RUNTIME_DIR/bundle-ctl/`: preferences saved from an agent window go to the copy.
- The shell outlives the agent: when claude quits, the agent's pane closes, its container stops
  and the window stays on the shell. A takeover by a VS Code window or another terminal closes the
  whole window instead (Seb, 2026-10-13): the wrapper sends SIGUSR1 to `agent-terminator.py`
  (`BUNDLE_CTL_WINDOW_PID`), which ends its terminator.
- With wmctrl, the window opens minimized (Seb, 2026-10-14), so that a new agent does not take
  the screen: it waits in the window list (below). The minimize is the `XIconifyWindow` of libX11,
  as wmctrl has none, sent within 0.1 s of the window showing.
- Each pane says where it runs. The two panes of the layout carry a title, `in the container:
  claude` and `on the host: shell`. A shell of the window, one split by hand included, also
  starts on `On the host, outside the container of BUNDLE.` and on `ocode BUNDLE opens its VS
  Code window.`, which carries the same link as `gbs`: the launcher exports
  `BUNDLE_CTL_AGENT_WINDOW`, and `.bashrc` prints the two lines in an interactive shell that
  holds it.
- The window title tells the windows apart in the task bar: an icon of what the work is about,
  the bundle, then that kind in words, as `📝 master-action-panel-aku (review)`. The word comes
  last, so that a narrow task bar button still shows the bundle. `client/window-title.py BUNDLE`
  reads the kind from the name alone. claude-autoopen gives the same title to the VS Code window
  of the bundle.
- In front of that icon, a mark says what Claude does in the window, so that the task bar shows
  which agents work, which are done and which wait for Seb: `⏳ 📝 master-action-panel-aku
  (review)`. It comes from the `status` Claude writes for each live session in
  `~/.claude/sessions/<pid>.json`, a folder of each container (`devcontainer/README.md`), and for
  an idle one from the first line of its last answer.
  `client/window-title.py BUNDLE SESSION=STATUS...` picks it. With wmctrl, `agent-terminator.py`
  opens the window on 🚀 and follows the session of its claude every second until the window
  closes, and keeps the last mark once that claude ended. claude-autoopen follows the sessions of
  its container.
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
| ⏳ | one works: `busy`, or `shell` while a background command runs |
| ✋ | one waits for a permission or an answer (`waiting`), or all are idle and one last answer is no `pushed:` nor `nothing to do:` (`ready to push`, `needs Seb:`, `blocked:`, a chat) |
| ✅ | all are idle on `pushed:` or `nothing to do:` |
| ❌ | the run of an agent window ended before its claude started (`.agent/done` is `failed`) |
| none | no live session |

A minimized agent window leaves the task bar and Alt+Tab, and comes back when it is restored:
25 agent windows left the task bar no room to show a title. To reach them:

- `window-picker.py`, a rofi script, lists the bundles first by mark (✅, ✋, ❌, ⏳, 🚀), one row
  each, then the other windows. A bundle open in VS Code has no row (Seb, 2026-10-13): he works
  there, and its window is in the task bar. A terminal keeps its last mark once its claude ends,
  so a ✅, ✋ or ⏳ shows only while an `agent-terminal.py BUNDLE` process runs. The search also
  takes the mark in words (`finished`, `waiting`, `failed`, `running`, `starting`), the kind is
  already in the title, and the line above the rows counts the bundles of each mark.
  `window-picker.py --show` opens it in rofi, where the row under the mouse is selected: a click on
  a bundle brings its VS Code window up, or opens it through its `odoo-bundle://` link, and a right
  click or Shift+Enter brings its terminal up. Until that VS Code window is up, the row of the
  bundle shows 🔄 (a file per bundle in `$XDG_RUNTIME_DIR/bundle-ctl/opening`, 5 minutes at most,
  dropped once the window is up). A Cinnamon shortcut runs it.
- `window-applet/`, a Cinnamon applet, shows the mark that needs Seb most and how many bundles
  have it, in the order ✋ ❌ ⏳ 🚀 ✅ (Seb, 2026-10-13: ✅ is pushed or nothing to do), or 🪟 when
  no bundle has a mark. Its tooltip has the counts of each mark, and a click opens the same list.
  The label comes from `window-picker.py --counts` every 2 seconds. It also sets `skip_taskbar` on
  an agent window (its terminator runs on a `bundle-ctl/terminator-` config) while it is
  minimized. It is a link to that folder,
  `~/.local/share/cinnamon/applets/bundle-windows@useful-things`, added to a panel in the
  `enabled-applets` key of `org.cinnamon`.

`python3 test_window_picker.py` runs each case.

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
`handoff`. A session with no record counts as idle once its last turn ended 2 minutes ago: two
containers whose claude has the same pid write the same `sessions/<pid>.json`. The terminal window closes, its shell too, and from
there on the bundle has an agent window (below): the tab opens on the session and, for a stopped
tool call, reruns the turn after one reload. The container then lives as long as that window.

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
memory (the task then waits for `memory on the host`). A bundle open in a VS Code window does not count, a session a window took over included: the cap
bounds what the agents spend, and Seb's windows are his.
`--model haiku|sonnet|opus` (default `opus`) is the model of the task's agent, for the one that
queues to send a job that needs less to a model that costs less. `--effort low|medium|high|xhigh`
(default `medium`) is how much that agent thinks: `medium` is where Anthropic's guide starts on
the three models, and the `xhigh` of Seb's settings stays for his own sessions. The daemon refuses
any other name, `max` included, and `client/agent-run.sh` runs on `opus` at `medium` when
`.agent/model` or `.agent/effort` holds anything else. `python3 test_options.py` runs each case.
`--priority N` (-100 to 100, default 0) puts the task ahead of the ones with a lower priority; equal
priorities leave in arrival order. The answer is the task's place (`queued`) and what it waits for
(`waits_for`), or `started` when a slot was free. `bctl status` lists the queue in that order with
each priority, the bundle that queued it, since when and what it waits for, so a caller can pick
where it goes.
A task for a bundle whose agent has not ended is queued behind it, so the caller has nothing to
try again: it waits for `its agent to end`, the verdict of an agent in a window, and for an agent
in a terminal `then its terminal to close`. A bundle holds one queued task: a second one is
refused.
One agent starts at a time, as several containers and claude sessions that start together take
the memory of the host at once: the next task waits for `the agent of BUNDLE to start`, until the
run of that agent began (its `.agent/run.lock`), its launch failed, or 15 minutes passed.
`bctl force-queue` lets the tasks queued now start over the cap and while the host is short of
memory, still one at a time and each
after what else it waits for; `bctl status` shows them `over the cap`, and a task queued later
waits for a slot again. Only a process of the host may ask it: a container gets a 403, the one
of `BUNDLE_CTL_HOST_FOLDER` too, so that going over the cap stays Seb's call. `bctl queue` lists
the queue alone, at once, and ends on the memory of the host: `the host is short of memory` when
less is available than the runs keep free, or when tasks stalled on memory 5% of the last minute
or more.
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
A queued task whose bundle folder is gone is dropped, and so is one its agent already runs (a pass
that dies after a launch saves no state): `python3 test_queue.py` runs a pass on each case, and
real clients that read and force the queue.
A new task moves the previous
`.agent/` files of the bundle into `.agent/history/`, unless it is the same task relaunched before
any run started. `bctl open BUNDLE --resume` leaves `session` in place: the terminal goes on with
that session (`agent-run.sh` resumes a session that has a transcript), and the task is its next
prompt, `Continue from where you left off.` when none is given. This is how an agent cut before its
verdict (`stopped`) starts again with what it knew. It is refused when the bundle has no session,
or is open in a window, where the session goes on in its Claude tab.
Closing the terminal or the window of an agent is Seb's call, and nothing deletes a bundle
without his yes, nor stops the container of a session he can still type in. `done` only says the
run reached its verdict, as Seb often goes on in the session. A task for a bundle whose last
session still has its terminal waits in the queue until that terminal closes, whether that session
reached its verdict or not.

The agent runs of all the containers start at least 10 seconds apart (a lock in the shared
`~/.claude`), as claude processes started together fail each other's OAuth token refresh. A
`claude -p` run that still fails with `Failed to refresh OAuth token` or `Failed to authenticate`
before its first tool call starts again 1 to 2 minutes later with a new session, at most 3 times.
In a terminal the failure shows on screen, and nothing retries it.
