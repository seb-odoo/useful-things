# agentbar

A Claude Code mod: a band above the prompt of an agent terminal, a bundle or an ops agent window
(Seb, 2026-10-21: "from the agent terminal, I would like a way to easily 1. see which pending hok
requests are related to this agent + link to open+focus this hok request on the app 2. link to open
firefox group tab for all relevant bundle pages", then "and maybe a button to focus the current
agent in the agent list").

```
🔑 g12  GITHUB  push 2 commits to master-x--seb of odoo-dev/odoo  5 min
🔁 f3  FIREFOX  open 3 pages in Firefox as one tab group  2 h, later
📋 agent list   🦊 tabs
```

- Each request of `hok` that waits for this agent, with where a yes acts, what it does and its age:
  `🔁` and dim when it is put off. Its name is a link to its row in the window of `hok`
  (`odoo-bundle://hok/NAME`). Four at most, then how many more.
- `📋 agent list` brings up the agent list on the row of this agent (`odoo-bundle://list/NAME`),
  and `🦊 tabs`, for a bundle, its Firefox tab group with the pages it lacks
  (`odoo-bundle://tabs/BUNDLE`). `scripts/bundle_open.py` opens the three on the host.
- A Ctrl+click opens a link, as for any link of the terminal. The band shows only in a terminal:
  VS Code draws a link that is not https as plain text.

Every 10 seconds it asks bundle-ctl, on its socket in the container (`/run/bundle-ctl/ctl.sock`,
with `curl`): `GET /whoami` once, then `GET /hok`, the requests of the bundle, or for the ops
container `GET /hok?session=` with the session of the conversation. A session with no socket, or
one of the host outside the ops container, has no band. Before bundle-ctl has `GET /hok`, the band
holds the links alone.

`client/agent-run.sh --terminal` and the ops agent windows of `agent-terminal.py` start claude with
`--plugin-dir` on this folder: a window opened before has no band until its claude starts again.

```bash
claude plugin test bundle-ctl/agentbar      # the band of a bundle, of an ops agent, none on the host
```
