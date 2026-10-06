# git-sign

Commits on the host are signed with an SSH key (the dev containers do not sign). Bitwarden asks
for each signature ("Always"), so here a signing-only key, kept in Bitwarden, is loaded once per
boot into an ssh-agent that holds it in memory until reboot.

- `gsign` asks the master password (bw CLI), loads `github-seb-odoo-signing` into
  `ssh-agent-sign.service`, and locks the CLI again. The key goes through a pipe, never a file.
- `sign` is `gpg.ssh.program`: it signs with that agent, or asks Bitwarden before `gsign` ran.

The key can sign but not push: GitHub knows it as a "Signing Key" only.

## Install on the host

Once:

1. `sudo snap install bw`, then `bw login`.
2. Bitwarden app: new item, type SSH key, named `github-seb-odoo-signing`.
3. `ln -sfn ~/repo/useful-things/git-sign/ssh-agent-sign.service ~/.config/systemd/user/`,
   then `systemctl --user daemon-reload && systemctl --user enable --now ssh-agent-sign.service`.
4. `gsign`, then `SSH_AUTH_SOCK=$XDG_RUNTIME_DIR/ssh-sign.sock ssh-add -L > ~/.ssh/github-seb-odoo-signing.pub`.
5. GitHub, Settings > SSH and GPG keys > New SSH key, type "Signing Key", paste that public key.
6. `git config --file ~/.gitconfig.local gpg.ssh.program $HOME/repo/useful-things/git-sign/sign`
   (git does not expand `~` there) and
   `git config --file ~/.gitconfig.local user.signingKey $HOME/.ssh/github-seb-odoo-signing.pub`.

Then `gsign` once per boot.
