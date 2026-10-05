# bundle-ctl

Lets an agent in a bundle's dev container start other bundles, their containers and their agents.
A container sees only its own bundle folder and reaches neither podman nor the host's VS Code, so a
new bundle, its worktrees and its window only come from the host: `daemon.py` makes them on its
behalf, behind a few fixed verbs. The git work itself (fetch, rebase, cherry-pick) stays with the
agent of each window, as VS Code forwards the SSH agent into the containers it attaches to.

- The daemon listens on `~/.local/state/bundle-ctl/sock/ctl.sock`. Every bundle container mounts
  that folder read-only at `/run/bundle-ctl`: a folder and not the socket, so a daemon restart needs
  no container rebuild, and read-only, so a container cannot replace the socket.
- The caller is identified by the kernel: the peer pid of the connection, its cgroup
  (`libpod-<id>.scope`), then the `devcontainer.local_folder` label of that container. A process on
  the host is `host`; any container that is not a bundle gets 403.
- One journal line per request: `journalctl --user -u bundle-ctl -f`.

## Install

See the header of `bundle-ctl.service`. It runs with the odoo20 venv, which the scripts need.

## Verbs

| verb | answer |
| --- | --- |
| `GET /whoami` | the caller's bundle, base and parent |
| `GET /status` | every local bundle branch: base, behind and conflict per repo, folder, open |
| `POST /create` | `gnb` with `--no-push`, then a window; refused when the bundle exists |
| `POST /fetch` | `pfb` on a bundle name, a PR link or a fork label, then a window; refused when the folder exists or a branch has unpushed commits |
| `POST /open` | a VS Code window on a bundle folder |
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

## Agent windows

A bundle whose folder holds `.agent/task.md` opens as an agent window. The Claude tab cannot start
work by itself (`claude-vscode.editor.open` only fills its input), so claude-autoopen runs the task
with `claude -p` in a terminal named "agent" and opens the tab on that session when it ends:

| file in `.agent/` | written by | meaning |
| --- | --- | --- |
| `task.md` | bundle-ctl | the prompt |
| `session` | claude-autoopen | the session id, chosen before the run |
| `run.lock` | `client/agent-run.sh` | the run started; a relaunched terminal does not run it again |
| `result.md` | `client/stream-format.py` | the last answer of the run |
| `mode` | `client/stream-format.py` | the permission mode of the run, which the tab resumes the session in |
| `done` | `client/agent-run.sh` | the exit code of `claude -p`, or `interrupted` |
| `tab-opened` | claude-autoopen | the tab was opened on the session once |

No empty Claude tab opens while the run lasts, so a window holds one Claude process at a time. The
run starts by itself and its log streams in the "agent" terminal; Ctrl+C there stops it, and the
tab opens on its session to take over.

`bctl create|fetch|open ... --task-file FILE` queues the task, and the daemon opens agent windows
from the queue while fewer than `BUNDLE_CTL_MAX_WINDOWS` (default 4) bundle windows are open, Seb's
own included, plus the agent windows launched in the last 15 minutes whose container is not up yet.
The cap is read from `~/.config/odoo-dev/config.env` or the unit's environment. The queue is checked
again on every podman container start and stop, and every minute; it lives in
`~/.local/state/bundle-ctl/state.json`. An agent run cannot queue a task before it ends, so a
fan-out is one level deep; the session Seb continues in the tab afterwards can. A task given to a
bundle whose window is open starts there within 5 seconds. A new task moves the previous `.agent/`
files of the bundle into `.agent/history/`. Closing an agent window is Seb's call: nothing stops or
deletes a bundle on its own.
