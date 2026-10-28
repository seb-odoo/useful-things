# git-sign

Commits on the host are signed with an SSH key (the dev containers do not sign): a signing-only
key, kept in Bitwarden and held by ssh-agent-gate (its own repo) from the login to the reboot.

- `sign` is `gpg.ssh.program`: it signs with the agent of ssh-agent-gate. A commit of a host-ctl
  request is signed at once, Seb said yes to the request; any other commit waits for a card in
  `hok`, as the rules of the gate say.
- At login, `ssh-agent-gate-load` asks the master password and loads the key with the others. A
  commit refused with "the keys are not loaded" means it did not run since the boot, or since
  the gate restarted.

The key can sign but not push: GitHub knows it as a "Signing Key" only.

## Install on the host

Once, ssh-agent-gate installed:

1. Bitwarden: new item, type SSH key, named `github-seb-odoo-signing`. Its public key in
   `~/.ssh/github-seb-odoo-signing.pub`, and in the rules of the gate.
2. GitHub, Settings > SSH and GPG keys > New SSH key, type "Signing Key", paste that public key.
3. `git config --file ~/.gitconfig.local gpg.ssh.program $HOME/repo/useful-things/git-sign/sign`
   (git does not expand `~` there) and
   `git config --file ~/.gitconfig.local user.signingKey $HOME/.ssh/github-seb-odoo-signing.pub`.
