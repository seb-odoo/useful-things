# Claude Auto-Open

In-house dev-container helper. On window open it opens a Claude Code editor tab when none is already
open, plus one terminal per repo (cd'd into each `/workspace/<repo>` worktree), so a freshly built
container comes up with Claude ready and a shell in every repo. Anything already restored or present
(the Claude tab, a repo's terminal) is left alone, so a window reload never duplicates them. Not
published; installed only inside the Odoo dev container.

## What it does at start

Once VS Code says its start is finished, the extension waits two seconds for the restored tabs and
terminals, then:

1. opens one terminal per repo of the bundle, in `/workspace/<repo>`, skipping a repo that already
   has a terminal of that name;
2. opens a Claude tab when no tab of the Claude panel is there;
3. pins that tab and unlocks its editor group, so that files open next to it and not in a new
   column. Both commands only act on the active editor, so they run only when the Claude tab is
   the active one;
4. closes the "Configuring..." terminal the Dev Containers extension leaves behind. A start that
   fails before the extension runs keeps it, so its error stays visible.

## Agent mode

When bundle-ctl opened the window for a task (`/workspace/.agent/task.md` exists), the extension
does not open an empty tab. It starts the agent, or takes over the one bundle-ctl runs in a
terminal, and opens the tab on the session of that agent once the run is done or handed over. A
pinned Claude tab that is already there is reused, unless a session of the window is busy:
closing a tab stops its turn. The rest is in [`bundle-ctl/`](../bundle-ctl/).

## Install

`install-claude-autoopen.sh` installs the VSIX through the remote CLI of the running VS Code
server, and only when its `extension.js` differs from the installed one. The extension runs the
script itself when it activates, so an edit reaches a container at its next window open. A
container built for the first time needs one window reload before the extension is active.

Rebuild the VSIX after editing `extension.js`:

```
cd useful-things/claude-autoopen && npx --yes @vscode/vsce package --allow-missing-repository --out claude-autoopen-0.0.1.vsix
```

## Why an extension

The `code` CLI has no flag to run a command or to open a URL in a running window, on the host or
in a container, so a shell hook cannot open the Claude tab. Only code that runs inside the editor
can.
