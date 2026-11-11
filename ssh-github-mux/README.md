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
  then 8 in an hour while he was at the machine and left the prompt alone. So the unit leaves
  `request/held` when a run ended with no socket (the keys are not loaded, the network is down),
  and `here`, the `ExecCondition` of the unit, holds the next start while that file is there.
- `here` held the start too while the Cinnamon screensaver was active, until 2026-11-11: since
  10-28 a reconnect asks no card, so the hold saved nothing, and that day the screensaver crashed
  at an undock and came back saying "active" for good: GitHub stayed down for the bundles from
  12:33 to the evening, the bundles' asks and Seb's own all held (his pick: drop the test).
- While `request/held` is there, only an ask of Seb himself goes through: `request/now`, written
  by `wait` for a git command of the host, and by `on-unlock` at unlock
  (`ssh-github-mux-unlock.service`). The queue and the agents write `request/start`, held until
  then, and their tasks wait in the queue as they already do.
- Where git-gate is installed, its fragment replaces the one at the root of this repo, and no
  container mounts any of this. Without it, that fragment gives containers this folder as
  `/etc/ssh/ssh_config.d` and as `~/.local/share/ssh-github-mux`, `sock/` read-only and `request/`
  read-write: a container uses the connection but cannot put a socket the host would trust. So
  `wait`, `on-unlock` and `here` write `request/` by renaming a file of `~/.local/state` over the
  name: a link a container left there is replaced, never followed (`test_request.py`). The marks
  of `sock/` (`up`, `stopped`) and the `request/held` of the unit are written the same way
  (security check 11-03, the S4 leftovers).
- ssh exits 255 both when GitHub closes the connection and when the key is refused (the gate
  holds no key, no agent). The service is started once the socket is there (`ExecStartPost`): an
  exit before is a failed start, and the unit shows as failed. An exit after, the normal end of
  the connection, leaves it inactive (`SuccessExitStatus=255`), as `systemctl stop` does.
- A crash of the host skips `ExecStopPost`, and `sock/github` stays on disk with no ssh behind it
  (2026-11-05: the laptop crashed at 12:49, and the unit stayed inactive all afternoon, as every
  fetch took the file for the connection). So the unit removes the file before ssh starts (ssh
  disables multiplexing over a file that is there), and what reads the connection connects to
  the socket rather than testing the file: `wait` with `ssh -O check`, the queue of bundle-ctl
  and git-gate with a `connect`.
- A connection can stay up and open no new session: on 2026-11-03 it kept its keepalives from
  10:51 to 12:11 while every git command through it hung, and nothing logged a line. So while it
  is up, `ssh-github-mux-probe.timer` runs `probe` every 2 minutes: a session through the socket
  must get GitHub's greeting within 20 seconds, else the unit restarts, and the sessions that
  still run through it end. The journal of `ssh-github-mux-probe` keeps what `ssh -v` said, which
  shows whether the master got its session from GitHub. That restart passes a `request/held`
  (`sock/probe`, read by `here`): the keys are loaded, a reconnect asks no card. When the next
  probe finds the new connection stuck too, a desktop notification says so.

Other hosts (pi5, test.upgrade, the `github-private` alias) are not concerned: one approval per
command.

The agent is ssh-agent-gate, its own repo, with its socket at
`$XDG_RUNTIME_DIR/ssh-agent-gate/sock`. This connection and the entries of `~/.ssh/config` name
that path (`IdentityAgent`). It must never be the `SSH_AUTH_SOCK` of the session: VS Code relays
the agent of its environment into every container it attaches to.

## Install on the host

```sh
ln -sfn ~/repo/useful-things/ssh-github-mux ~/.local/share/ssh-github-mux
sed -i '1i Include ~/.local/share/ssh-github-mux/github-mux.conf\n' ~/.ssh/config
sh ~/repo/useful-things/ssh-github-mux/install
systemctl --user enable --now ssh-github-mux.path ssh-github-mux-unlock.service
```

`install` links the units in `~/.config/systemd/user` and reloads them. A deploy of this folder
runs it too (`after-deploy` of `host-ops.json`). Tests: `python3 test_request.py`,
`python3 test_probe.py`.

Stop the connection: `systemctl --user stop ssh-github-mux`.
