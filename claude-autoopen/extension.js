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

function getClaudeTab() {
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const input = tab.input;
      if (input instanceof vscode.TabInputWebview && input.viewType.includes(CLAUDE_VIEWTYPE)) {
        return tab;
      }
    }
  }
  return undefined;
}

async function openRepoTerminals() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    return;
  }
  const root = folders[0].uri;
  // Skip repos that already have a terminal (restored after a window reload) so we never
  // duplicate, mirroring hasClaudeTab()'s "open only if absent" guard.
  const open = new Set(vscode.window.terminals.map((terminal) => terminal.name));
  // The extensions cache is shared by every dev container, so this also activates in
  // non-bundle workspaces (e.g. workflow-hub) where the repo cwds don't exist and each
  // createTerminal would pop an error. One stat on the first repo decides: every bundle has
  // all 6 worktrees (config.py folder_by_repo), so a single check suffices (per-repo stats
  // are remote round-trips that staggered the terminals).
  let isBundle = true;
  try {
    await vscode.workspace.fs.stat(vscode.Uri.joinPath(root, REPOS[0]));
  } catch {
    isBundle = false;
  }
  if (!isBundle) {
    if (!open.has("workspace")) {
      vscode.window.createTerminal({ name: "workspace", cwd: root }).show(true);
    }
    return;
  }
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
  await openRepoTerminals();
  if (!getClaudeTab()) {
    const claude = vscode.extensions.getExtension(CLAUDE_EXTENSION_ID);
    if (!claude) {
      return;
    }
    if (!claude.isActive) {
      await claude.activate();
    }
    await vscode.commands.executeCommand(OPEN_COMMAND);
    // The freshly opened panel becomes the active editor, but the reveal is async: wait so it is
    // enumerated as the active tab before we pin it.
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  // Pin the Claude tab so it stays at the front and isn't replaced by preview editors.
  // workbench.action.pinEditor targets the ACTIVE editor (there is no API to pin an arbitrary tab),
  // so guard on isActive to avoid pinning the wrong editor when a restored Claude tab isn't focused.
  // VS Code persists pin state across reloads, so !isPinned keeps this idempotent.
  const tab = getClaudeTab();
  if (tab && tab.isActive) {
    if (!tab.isPinned) {
      await vscode.commands.executeCommand("workbench.action.pinEditor");
    }
    // The claude-code extension locks the editor group when it opens the panel into a new column
    // (workbench.action.lockEditorGroup). A locked group refuses other editors, so files open in a
    // separate column. We pin the tab to keep it in place, so undo the lock and let files open
    // normally here. unlockEditorGroup targets the ACTIVE group; the isActive guard keeps it on the
    // Claude group, and it is a no-op when the group is already unlocked, so this stays idempotent.
    await vscode.commands.executeCommand("workbench.action.unlockEditorGroup");
  }
}

function deactivate() {}

module.exports = { activate, deactivate };
