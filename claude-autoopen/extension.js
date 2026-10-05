const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const vscode = require("vscode");

// The claude-code extension creates its panel with viewType "claudeVSCodePanel"; VS Code reports it
// on a restored tab as "mainThreadWebview-claudeVSCodePanel", so match by substring (the claude-code
// extension checks the same way internally).
const CLAUDE_VIEWTYPE = "claudeVSCodePanel";
const CLAUDE_EXTENSION_ID = "anthropic.claude-code";
const OPEN_COMMAND = "claude-vscode.editor.open";
// bundle-ctl writes the task of an agent window there (bundle-ctl/README.md).
const AGENT_FOLDER = ".agent";
const AGENT_TERMINAL = "agent";

// Every /workspace bundle is created with these repo worktrees (useful-things/scripts/config.py
// folder_by_repo). One terminal each, cd'd into the worktree.
const REPOS = [
  "odoo",
  "enterprise",
  "owl",
  "design-themes",
  "documentation",
  "upgrade",
  "upgrade-util",
  "sfu",
];

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
  // createTerminal on a missing cwd pops an error, and a workspace can lack any of these repos:
  // a folder opened with fcode can have none, a bundle older than a repo's folder_by_repo entry has
  // all but that one.
  let entries;
  try {
    entries = await vscode.workspace.fs.readDirectory(root);
  } catch {
    entries = [];
  }
  const present = new Set(entries.map(([name]) => name));
  const repos = REPOS.filter((repo) => present.has(repo));
  if (!repos.length) {
    if (!open.has("workspace")) {
      vscode.window.createTerminal({ name: "workspace", cwd: root }).show(true);
    }
    return;
  }
  let first;
  for (const repo of repos) {
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

async function readAgentFile(root, name) {
  try {
    const data = await vscode.workspace.fs.readFile(vscode.Uri.joinPath(root, AGENT_FOLDER, name));
    return new TextDecoder().decode(data).trim();
  } catch {
    return undefined;
  }
}

async function writeAgentFile(root, name, text) {
  const uri = vscode.Uri.joinPath(root, AGENT_FOLDER, name);
  await vscode.workspace.fs.writeFile(uri, new TextEncoder().encode(text));
}

async function runAgent(root) {
  const client = process.env.BUNDLE_CTL_CLIENT;
  if (
    !client ||
    (await readAgentFile(root, "task.md")) === undefined ||
    (await readAgentFile(root, "done")) !== undefined
  ) {
    return false;
  }
  let session = await readAgentFile(root, "session");
  const agentTerminals = vscode.window.terminals.filter(
    (terminal) => terminal.name === AGENT_TERMINAL,
  );
  if (!session) {
    session = crypto.randomUUID();
    await writeAgentFile(root, "session", session);
    agentTerminals.forEach((terminal) => terminal.dispose());
  } else if (agentTerminals.length) {
    return true;
  }
  vscode.window
    .createTerminal({
      name: AGENT_TERMINAL,
      cwd: root,
      shellPath: "/bin/bash",
      shellArgs: [`${client}/agent-run.sh`, session],
    })
    .show(true);
  return true;
}

async function takeAgentSession(root) {
  const session = await readAgentFile(root, "session");
  if (
    !session ||
    (await readAgentFile(root, "done")) === undefined ||
    (await readAgentFile(root, "tab-opened")) !== undefined
  ) {
    return undefined;
  }
  await writeAgentFile(root, "tab-opened", "");
  return session;
}

// Resume the agent session in the mode of its run: the Claude tab reads it from this store of the
// Claude extension, and falls back to claudeCode.initialPermissionMode.
async function seedSessionMode(context, root, session) {
  const mode = await readAgentFile(root, "mode");
  if (!mode) {
    return;
  }
  const store = vscode.Uri.joinPath(
    context.globalStorageUri,
    "..",
    CLAUDE_EXTENSION_ID,
    "session-permission-modes",
  );
  await vscode.workspace.fs.createDirectory(store);
  await vscode.workspace.fs.writeFile(
    vscode.Uri.joinPath(store, `${session}.json`),
    new TextEncoder().encode(JSON.stringify({ mode, updatedAt: Date.now() })),
  );
}

async function openClaudeTab(sessionId) {
  const claude = vscode.extensions.getExtension(CLAUDE_EXTENSION_ID);
  if (!claude) {
    return;
  }
  if (!claude.isActive) {
    await claude.activate();
  }
  await vscode.commands.executeCommand(OPEN_COMMAND, sessionId);
  // The freshly opened panel becomes the active editor, but the reveal is async: wait so it is
  // enumerated as the active tab before we pin it.
  await new Promise((resolve) => setTimeout(resolve, 500));
}

async function activate(context) {
  // Let restored editor/webview tabs settle before deciding whether a Claude tab already exists,
  // otherwise a restored tab might not be enumerated yet and we would open a duplicate. The same
  // wait also lets restored terminals enumerate before openRepoTerminals() decides which to open.
  await new Promise((resolve) => setTimeout(resolve, 2000));
  watchOwnCode(context);
  await openRepoTerminals();
  const root = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (root) {
    // Poll, as the file watcher misses the .agent folder bundle-ctl creates from the host.
    let polling = false;
    const timer = setInterval(async () => {
      if (!polling) {
        polling = true;
        await pollAgent(context, root).finally(() => (polling = false));
      }
    }, 5000);
    context.subscriptions.push({ dispose: () => clearInterval(timer) });
    if (await runAgent(root)) {
      return;
    }
  }
  await showClaudeTab(context, root);
}

async function pollAgent(context, root) {
  if ((await readAgentFile(root, "session")) === undefined) {
    await runAgent(root);
  } else if (
    (await readAgentFile(root, "done")) !== undefined &&
    (await readAgentFile(root, "tab-opened")) === undefined
  ) {
    await showClaudeTab(context, root);
  }
}

// Offer a reload when a container installs a new version in the shared extensions folder.
function watchOwnCode(context) {
  const file = path.join(context.extensionPath, "extension.js");
  const listener = (current, previous) => {
    if (current.mtimeMs === previous.mtimeMs) {
      return;
    }
    fs.unwatchFile(file, listener);
    vscode.window
      .showInformationMessage(
        "Claude Auto-Open was updated: reload the window to use it.",
        "Reload",
      )
      .then((choice) => {
        if (choice === "Reload") {
          vscode.commands.executeCommand("workbench.action.reloadWindow");
        }
      });
  };
  fs.watchFile(file, { interval: 10000 }, listener);
  context.subscriptions.push({ dispose: () => fs.unwatchFile(file, listener) });
}

async function showClaudeTab(context, root) {
  const session = root && (await takeAgentSession(root));
  if (session) {
    await seedSessionMode(context, root, session);
  }
  if (session || !getClaudeTab()) {
    await openClaudeTab(session);
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
