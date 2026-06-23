const vscode = require("vscode");

// The claude-code extension creates its panel with viewType "claudeVSCodePanel"; VS Code reports it
// on a restored tab as "mainThreadWebview-claudeVSCodePanel", so match by substring (the claude-code
// extension checks the same way internally).
const CLAUDE_VIEWTYPE = "claudeVSCodePanel";
const CLAUDE_EXTENSION_ID = "anthropic.claude-code";
const OPEN_COMMAND = "claude-vscode.editor.open";

// Every /workspace bundle is created with exactly these 6 repo worktrees
// (useful-things/scripts/config.py folder_by_repo). One terminal each, cd'd into the worktree.
const REPOS = ["odoo", "enterprise", "design-themes", "documentation", "upgrade", "upgrade-util"];

function hasClaudeTab() {
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const input = tab.input;
      if (input instanceof vscode.TabInputWebview && input.viewType.includes(CLAUDE_VIEWTYPE)) {
        return true;
      }
    }
  }
  return false;
}

function openRepoTerminals() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    return;
  }
  const root = folders[0].uri;
  // Skip repos that already have a terminal (restored after a window reload) so we never
  // duplicate, mirroring hasClaudeTab()'s "open only if absent" guard.
  const open = new Set(vscode.window.terminals.map((terminal) => terminal.name));
  // No per-repo existence check: every bundle has all 6 worktrees (config.py folder_by_repo), and a
  // vscode.workspace.fs.stat() is a remote round-trip that, run per repo, staggered the terminals.
  let first;
  for (const repo of REPOS) {
    if (open.has(repo)) {
      continue;
    }
    const terminal = vscode.window.createTerminal({
      name: repo,
      cwd: vscode.Uri.joinPath(root, repo),
    });
    if (!first) {
      first = terminal;
    }
  }
  // Reveal the panel but keep focus on the editor / Claude tab.
  if (first) {
    first.show(true);
  }
}

async function activate() {
  // Let restored editor/webview tabs settle before deciding whether a Claude tab already exists,
  // otherwise a restored tab might not be enumerated yet and we would open a duplicate. The same
  // wait also lets restored terminals enumerate before openRepoTerminals() decides which to open.
  await new Promise((resolve) => setTimeout(resolve, 2000));
  openRepoTerminals();
  if (hasClaudeTab()) {
    return;
  }
  const claude = vscode.extensions.getExtension(CLAUDE_EXTENSION_ID);
  if (!claude) {
    return;
  }
  if (!claude.isActive) {
    await claude.activate();
  }
  await vscode.commands.executeCommand(OPEN_COMMAND);
}

function deactivate() {}

module.exports = { activate, deactivate };
