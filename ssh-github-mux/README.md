# ssh-github-mux

ssh-agent-gate holds the key and asks a card in `hok` for each SSH connection, and `gfa` alone
opens one per repo. Here one connection to github.com, opened by the host, carries every git
command of the host and of the dev containers until it drops (reboot, suspend, network change),
and the gate lets this unit open it again with no card (Seb, 2026-10-28): git-gate still checks
each push of a container.

- `github-mux.conf`: for `github.com`, waits for the connection (`wait`), then goes through it.
  If it does not come within a minute, or the key was refused, ssh connects directly as before.
- `wait` writes the git command that asks in `request/now`, `ssh-github-mux.path` sees it and
  starts `ssh-github-mux.service`, the connection itself, with the key of ssh-agent-gate.
- An ask nobody answers dies after a minute, and the bundles that wait for GitHub ask again every
  10 minutes (the bctl queue) and at each fetch of an agent: Seb got a stream of asks while away,
  then 8 in an hour while he was at the machine and left the prompt alone. So `here`, the
  `ExecCondition` of the unit, holds the start and leaves `request/held` while the Cinnamon
  screensaver is active, and the unit leaves that file too when a run ended with no socket (the
  keys are not loaded, the network is down).
- While `request/held` is there, only an ask of Seb himself goes through: `request/now`, written
  by `wait` for a git command of the host, and by `on-unlock` at unlock
  (`ssh-github-mux-unlock.service`). The queue and the agents write `request/start`, held until
  then, and their tasks wait in the queue as they already do.
- Containers (`devcontainer/base.jsonc`) get this folder as `/etc/ssh/ssh_config.d` and as
  `~/.local/share/ssh-github-mux`, `sock/` read-only and `request/` read-write: a container uses
  the connection but cannot put a socket the host would trust.
- ssh exits 255 both when GitHub closes the connection and when the key is refused (the gate
  holds no key, no agent). The service is started once the socket is there (`ExecStartPost`): an
  exit before is a failed start, and the unit shows as failed. An exit after, the normal end of
  the connection, leaves it inactive (`SuccessExitStatus=255`), as `systemctl stop` does.

Other hosts (pi5, test.upgrade, the `github-private` alias) are not concerned: one approval per
command.

The agent is ssh-agent-gate, its own repo, with its socket at
`$XDG_RUNTIME_DIR/ssh-agent-gate/sock`. This connection and the entries of `~/.ssh/config` name
that path (`IdentityAgent`). It must never be the `SSH_AUTH_SOCK` of the session: VS Code relays
the agent of its environment into every container it attaches to.

## Install on the host

```sh
ln -sfn ~/repo/useful-things/ssh-github-mux ~/.local/share/ssh-github-mux
ln -sfn ~/repo/useful-things/ssh-github-mux/ssh-github-mux{.service,.path,-unlock.service} ~/.config/systemd/user/
sed -i '1i Include ~/.local/share/ssh-github-mux/github-mux.conf\n' ~/.ssh/config
systemctl --user daemon-reload && systemctl --user enable --now ssh-github-mux.path ssh-github-mux-unlock.service
```

Stop the connection: `systemctl --user stop ssh-github-mux`.
