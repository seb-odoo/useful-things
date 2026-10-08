# bundle-ctl

Lets an agent in a bundle's dev container start other bundles, their containers and their agents.
A container sees only its own bundle folder and reaches neither podman nor the host's VS Code, so a
new bundle, its worktrees, its container and its agent only come from the host: `daemon.py` makes
them on its behalf, behind a few fixed verbs. The git work itself (fetch, rebase, cherry-pick) stays
with the agent of each bundle: github.com goes through `ssh-github-mux`, and VS Code forwards the
SSH agent into the containers it attaches to.

- The daemon listens on `~/.local/state/bundle-ctl/sock/ctl.sock`. Every bundle container mounts
  that folder read-only at `/run/bundle-ctl`: a folder and not the socket, so a daemon restart needs
  no container rebuild, and read-only, so a container cannot replace the socket.
- The caller is identified by the kernel: the peer pid of the connection, its cgroup
  (`libpod-<id>.scope`), then the `devcontainer.local_folder` label of that container. A process on
  the host is `host`; any container that is not a bundle gets 403.
- One journal line per request: `journalctl --user -u bundle-ctl -f`. Also `address <ip> <name>`
  for each container that starts (and each one running when the daemon starts), the bundle or the
  container name behind an address, which the gateway logs see alone.

## Install

See the header of `bundle-ctl.service`. It runs with the odoo20 venv, which the scripts need.

## Verbs

| verb | answer |
| --- | --- |
| `GET /whoami` | the caller's bundle, base and parent |
| `GET /branches` | `gbs --json` run on the host; `?format=table&width=N`: its colored table |
| `GET /status` | every local bundle branch: base, behind and conflict per repo, folder, opening (launched in the last 15 minutes, container not up yet), open, and its agent: state (`running`, `waiting`, `done`, or `stopped` when its terminal or its window closed before the verdict: nothing runs it any more, and a new task may replace it; a stopped agent carries its `task` text, to queue it again as it was), when it was queued, when its run started (none for a task no window took), when it ended, `stale` when a branch of the bundle got a commit, or its dev remote ref an update (the mtime of its reflog), after that end, retry, and the last write to a session titled with the bundle, its own or an earlier agent's tab Seb went on in (`active N min ago`, or `idle since HH:MM` after 15 minutes; a tab opened by hand has no title and does not count); the queue, each task with its priority, the bundle that queued it and when |
| `POST /create` | `gnb` with `--no-push`, then a window, or with a task its agent; refused when the bundle exists |
| `POST /fetch` | `pfb` on a bundle name, a PR link or a fork label, then a window, or with a task its agent; refused when the folder exists or a branch has unpushed commits |
| `POST /open` | a VS Code window on a bundle folder, or with a task its agent |
| `GET /job` | the state and log of a create/fetch/open, long-polled by `bctl` |

`create`, `fetch` and `open` answer 202 with a job id, and the jobs run one at a time, as two
fetches of one repo collide on its remote refs. A job takes the `DISPLAY` and `SSH_AUTH_SOCK` of the
desktop session from `systemctl --user show-environment`, so it fails until Seb is logged in. The
daemon never pushes.

`bctl` (`client/bctl.py`, stdlib only, defined in `container-rw/devcontainer.bashrc`) calls them
from a container or from the host: `bctl whoami`, `bctl status`. The containers mount `client/`
read-only at its host path. Without the client:

    curl --unix-socket ~/.local/state/bundle-ctl/sock/ctl.sock http://bundle-ctl/whoami

## Wrong version

`client/check-base.sh` is a SessionStart hook of the shared bundle `.claude/settings.json`: when
the open PR of the bundle targets another base, it tells the session to `bctl create` a bundle on
that base with a task to cherry-pick the commits. The agent of the new window does the
cherry-pick, as all the bundles share the base repos' `.git`.

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
- A turn that ends on a verdict writes `done` (`client/agent-stop.py`), a turn that ends on
  anything else `waiting`. Either way the claude session stays open in its terminal, for Seb to
  read what ran and to type in, and its container keeps its slot.
- Quitting claude or closing the terminal stops the container. Without a verdict the agent then
  reads `stopped`.
- A claude that ends on an error leaves the terminal open on its exit code and on the tool calls
  and answers of the session (`client/stream-format.py` on its transcript): claude draws on the
  alternate screen, which empties when claude ends.

A VS Code window opened on the bundle (the folder icon of `gbs`, `ocode`) attaches to the same
container and takes the session over, before or after its verdict: claude-autoopen sees
`.agent/terminal` and runs `client/agent-takeover.py`. It stops the terminal's claude at once when
a tool call is in flight or the session is idle, else at the first of the two, and writes
`handoff`. The terminal closes, and from there on the bundle has an agent window (below): the tab
opens on the session and, for a stopped tool call, reruns the turn after one reload. The container
then lives as long as that window.

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
| `queued` | bundle-ctl | when the task entered the queue (epoch seconds), kept when a task no window took is queued again |
| `terminal` | `agent-terminal.py` | the agent runs in a terminal of the host; removed when it ends, or by `client/agent-takeover.py` when a window takes the session over |
| `session` | `agent-terminal.py`, claude-autoopen | the session id, chosen before the run |
| `run.lock` | `client/agent-run.sh` | the run started, its mtime is when; a relaunched terminal does not run it again |
| `result.md` | `client/stream-format.py`, `client/agent-stop.py` | the last answer of the run |
| `mode` | `client/stream-format.py`, `client/agent-takeover.py` | the permission mode of the run, which the tab resumes the session in |
| `handoff` | `client/agent-run.sh`, `client/agent-takeover.py` | claude was stopped for the tab to go on: `claude -p` at its first tool call, or the claude of a terminal |
| `retry` | `client/agent-run.sh` | `auth failed, retry n/3`: the run failed to authenticate and starts again |
| `waiting` | `client/agent-stop.py` | a turn of the terminal or of the tab ended without a verdict; its mtime is when |
| `done` | `client/agent-run.sh`, `client/agent-stop.py` | the exit code of `claude -p`, or `interrupted`; `0` when a turn of the terminal or of the tab ends on a verdict |
| `tab-opened` | claude-autoopen | the tab was opened on the session once |
| `restarted` | claude-autoopen, `client/agent-takeover.py` | the window was reloaded once for the handoff, or needs no reload (the session was idle when taken over) |

The reload waits while a Claude session of the container is busy or waits for a permission. If the
tab does not rerun the turn (a claude-code change), the session waits there with its prompt
unanswered: "continue" resumes it. `client/agent-stop.py`, a Stop hook of the bundles' Claude
settings (`/home/seb/src/odoo/.claude/settings.json`), writes `result.md` when a turn of the tab
ends, and `done` when its first line is a verdict: `ready to push`, `needs Seb:`, `blocked:` or
`nothing to do:`. A turn that ends on anything else (a background job still running) leaves
`waiting`. Ctrl+C in the "agent" terminal before the first tool call stops the run, and the tab
opens on its session. The "agent" terminal closes when the tab opens, unless `claude -p` failed: its
log is then the only trace of the error. The tab opens in place of a pinned Claude tab of the window, unless a Claude
session of the container is busy or waits for a permission.

`bctl create|fetch|open ... --task-file FILE` queues the task, and the daemon starts agents from
the queue while fewer than `BUNDLE_CTL_MAX_WINDOWS` (default 8) bundle containers run, the agents
in a terminal and Seb's windows alike, plus the agents launched in the last 15 minutes whose
container is not up yet.
`--priority N` (-100 to 100, default 0) puts the task ahead of the ones with a lower priority; equal
priorities leave in arrival order. The answer is the task's place (`queued`), or `started` when a
slot was free. `bctl status` lists the queue in that order with each priority, the bundle that
queued it and since when, so a caller can pick where it goes.
The cap is read from `~/.config/odoo-dev/config.env` or the unit's environment. The queue is checked
again on every podman container start and stop, and every minute; it lives in
`~/.local/state/bundle-ctl/state.json`. Any session can queue, agent runs included: the cap
is the only bound on a fan-out. A task given to a bundle whose window is open starts there within 5
seconds, even at the cap, as it takes no new container. A launched task nothing took (no
`.agent/session` once the launch is 15 minutes old and no container is up) goes back at the head
of the queue, 3 times at most: a terminal asked for while the desktop session ends never starts.
A new task moves the previous
`.agent/` files of the bundle into `.agent/history/`, unless it is the same task relaunched before
any run started. Closing the terminal or the window of an agent is Seb's call: nothing else stops a
container, and nothing deletes a bundle on its own. `done` only says the run reached its verdict,
as Seb often goes on in the session: when the queue waits on the cap, `bctl status` names the
agents that are done and idle, the ones he can close. A task for a bundle whose last session still
has its terminal waits in the queue until that terminal closes.

The agent runs of all the containers start at least 10 seconds apart (a lock in the shared
`~/.claude`), as claude processes started together fail each other's OAuth token refresh. A
`claude -p` run that still fails with `Failed to refresh OAuth token` or `Failed to authenticate`
before its first tool call starts again 1 to 2 minutes later with a new session, at most 3 times.
In a terminal the failure shows on screen, and nothing retries it.
