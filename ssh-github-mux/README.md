# ssh-github-mux

Bitwarden asks for each SSH connection ("Always"), and `gfa` alone opens one per repo. Here one
connection to github.com, opened by the host, carries every git command of the host and of the
dev containers: one approval until the connection drops (reboot, suspend, network change).

- `github-mux.conf`: for `github.com`, waits for the connection (`wait`), then goes through it.
  If it does not come within a minute, or Bitwarden refused, ssh connects directly as before.
- `wait` writes `request/start`, `ssh-github-mux.path` sees it and starts
  `ssh-github-mux.service`, the connection itself (the filtered agent, so the GitHub key only).
- While the screen is locked, nobody answers Bitwarden, and each ask times out after a minute.
  The bundles that wait for GitHub ask again every 10 minutes (the bctl queue) and at each fetch
  of an agent, so Seb got a stream of asks at his return. `here`, the `ExecCondition` of the
  unit, skips the start while Cinnamon's screensaver is active and leaves `request/held`.
  `ssh-github-mux-unlock.service` (`on-unlock`) writes `request/start` again at unlock when that
  file is there: one ask once he is back.
- Containers (`devcontainer/base.jsonc`) get this folder as `/etc/ssh/ssh_config.d` and as
  `~/.local/share/ssh-github-mux`, `sock/` read-only and `request/` read-write: a container uses
  the connection but cannot put a socket the host would trust.
- ssh exits 255 both when GitHub closes the connection and when the key is refused (Bitwarden
  said no, no agent). The service is started once the socket is there (`ExecStartPost`): an
  exit before is a failed start, and the unit shows as failed. An exit after, the normal end of
  the connection, leaves it inactive (`SuccessExitStatus=255`), as `systemctl stop` does.

Other hosts (pi5, test.upgrade, the `github-private` alias) are not concerned: one approval per
command.

The filtered agent is a unit of the host, `ssh-agent-github.service`: `ssh-agent-filter` in front
of Bitwarden, with its socket at `$XDG_RUNTIME_DIR/ssh-agent-github/sock`. Only this connection
and the `github.com` entry of `~/.ssh/config` name that path (`IdentityAgent`). It must never be
the `SSH_AUTH_SOCK` of the session: VS Code relays the agent of its environment into every
container it attaches to, where a signature goes around the connection and what filters it.

## Install on the host

```sh
ln -sfn ~/repo/useful-things/ssh-github-mux ~/.local/share/ssh-github-mux
ln -sfn ~/repo/useful-things/ssh-github-mux/ssh-github-mux{.service,.path,-unlock.service} ~/.config/systemd/user/
sed -i '1i Include ~/.local/share/ssh-github-mux/github-mux.conf\n' ~/.ssh/config
systemctl --user daemon-reload && systemctl --user enable --now ssh-github-mux.path ssh-github-mux-unlock.service
```

Stop the connection: `systemctl --user stop ssh-github-mux`.
