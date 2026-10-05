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

`bctl` (`client/bctl.py`, stdlib only, defined in `container-rw/devcontainer.bashrc`) calls them
from a container or from the host: `bctl whoami`, `bctl status`. The containers mount `client/`
read-only at its host path. Without the client:

    curl --unix-socket ~/.local/state/bundle-ctl/sock/ctl.sock http://bundle-ctl/whoami
