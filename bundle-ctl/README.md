# bundle-ctl

Lets an agent in a bundle's dev container ask the host for bundle work. A container reaches neither
the host network nor podman, and has no SSH key, so `gnb`, `pfb`, `ocode` and `gbs` only run on the
host. `daemon.py` runs them on its behalf, behind fixed verbs.

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
| `POST /fetch` | `pfb`, then a window; refused when the folder exists or a branch has unpushed commits |
| `POST /open` | a VS Code window on a bundle folder |
| `GET /job` | the state and log of a create/fetch/open, long-polled by `bctl` |

`create`, `fetch` and `open` answer 202 with a job id, and the jobs run one at a time, as two
fetches of one repo collide on its remote refs. A job takes the `DISPLAY` and `SSH_AUTH_SOCK` of the
desktop session from `systemctl --user show-environment`, so it fails until Seb is logged in. The
daemon never pushes.

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
| `done` | `client/agent-run.sh` | the exit code of `claude -p`, or `interrupted` |
| `tab-opened` | claude-autoopen | the tab was opened on the session once |

No empty Claude tab opens while the run lasts, so a window holds one Claude process at a time.

`bctl` (`client/bctl.py`, stdlib only, defined in `container-rw/devcontainer.bashrc`) calls them
from a container or from the host: `bctl whoami`, `bctl status`. The containers mount `client/`
read-only at its host path. Without the client:

    curl --unix-socket ~/.local/state/bundle-ctl/sock/ctl.sock http://bundle-ctl/whoami
