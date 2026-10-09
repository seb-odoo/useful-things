# ssh-github-mux

Bitwarden asks for each SSH connection ("Always"), and `gfa` alone opens one per repo. Here one
connection to github.com, opened by the host, carries every git command of the host and of the
dev containers: one approval until the connection drops (reboot, suspend, network change).

- `github-mux.conf`: for `github.com`, waits for the connection (`wait`), then goes through it.
  If it does not come within a minute, or Bitwarden refused, ssh connects directly as before.
- `wait` writes `request/start`, `ssh-github-mux.path` sees it and starts
  `ssh-github-mux.service`, the connection itself (the filtered agent, so the GitHub key only).
- Containers (`devcontainer/base.jsonc`) get this folder as `/etc/ssh/ssh_config.d` and as
  `~/.local/share/ssh-github-mux`, `sock/` read-only and `request/` read-write: a container uses
  the connection but cannot put a socket the host would trust.

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
ln -sfn ~/repo/useful-things/ssh-github-mux/ssh-github-mux.{service,path} ~/.config/systemd/user/
sed -i '1i Include ~/.local/share/ssh-github-mux/github-mux.conf\n' ~/.ssh/config
systemctl --user daemon-reload && systemctl --user enable --now ssh-github-mux.path
```

Stop the connection: `systemctl --user stop ssh-github-mux`.
