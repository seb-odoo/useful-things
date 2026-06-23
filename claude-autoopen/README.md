# Claude Auto-Open

Private dev-container helper. On window open it opens a Claude Code editor tab when none is already
open, plus one terminal per repo (cd'd into each `/workspace/<repo>` worktree), so a freshly built
container comes up with Claude ready and a shell in every repo. Anything already restored or present
(the Claude tab, a repo's terminal) is left alone, so a window reload never duplicates them. Not
published; installed only inside the Odoo dev container.

Rebuild the VSIX after editing `extension.js`:

```
cd useful-things/claude-autoopen && npx --yes @vscode/vsce package --allow-missing-repository --out claude-autoopen-0.0.1.vsix
```
