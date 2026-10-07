const childProcess = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
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

function getClaudeTab(predicate = () => true) {
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const input = tab.input;
      if (
        input instanceof vscode.TabInputWebview &&
        input.viewType.includes(CLAUDE_VIEWTYPE) &&
        predicate(tab)
      ) {
        return tab;
      }
    }
  }
  return undefined;
}

// The host and every container share this folder: keep the live processes of this pid namespace.
function getLiveSessions() {
  const config = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), ".claude");
  const folder = path.join(config, "sessions");
  let namespace;
  let names;
  try {
    namespace = fs.readlinkSync("/proc/self/ns/pid");
    names = fs.readdirSync(folder).filter((name) => name.endsWith(".json"));
  } catch {
    return [];
  }
  return names.flatMap((name) => {
    try {
      const record = JSON.parse(fs.readFileSync(path.join(folder, name), "utf8"));
      if (!record.pidDomain?.endsWith(`:${namespace}`)) {
        return [];
      }
      process.kill(record.pid, 0);
      return [record];
    } catch {
      return [];
    }
  });
}

function hasBusySession() {
  return getLiveSessions().some((record) => record.status !== "idle");
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
    (await isAgentStopped(root))
  ) {
    return false;
  }
  let session = await readAgentFile(root, "session");
  if (!session) {
    session = crypto.randomUUID();
    await writeAgentFile(root, "session", session);
    closeAgentTerminals();
  } else if (getAgentTerminals().length) {
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

function getAgentTerminals() {
  return vscode.window.terminals.filter((terminal) => terminal.name === AGENT_TERMINAL);
}

function closeAgentTerminals() {
  getAgentTerminals().forEach((terminal) => terminal.dispose());
}

async function isAgentStopped(root) {
  return (
    (await readAgentFile(root, "done")) !== undefined ||
    (await readAgentFile(root, "handoff")) !== undefined
  );
}

async function takeAgentSession(root) {
  const session = await readAgentFile(root, "session");
  if (
    !session ||
    !(await isAgentStopped(root)) ||
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

async function openClaudeTab(sessionId, viewColumn) {
  const claude = vscode.extensions.getExtension(CLAUDE_EXTENSION_ID);
  if (!claude) {
    return;
  }
  if (!claude.isActive) {
    await claude.activate();
  }
  await vscode.commands.executeCommand(OPEN_COMMAND, sessionId, undefined, viewColumn);
  // The freshly opened panel becomes the active editor, but the reveal is async: wait so it is
  // enumerated as the active tab before we pin it.
  await new Promise((resolve) => setTimeout(resolve, 500));
}

async function activate(context) {
  // Let restored editor/webview tabs settle before deciding whether a Claude tab already exists,
  // otherwise a restored tab might not be enumerated yet and we would open a duplicate. The same
  // wait also lets restored terminals enumerate before openRepoTerminals() decides which to open.
  await new Promise((resolve) => setTimeout(resolve, 2000));
  // Close the terminal Dev Containers leaves on "Press any key", even after a host-side command.
  vscode.window.terminals.find((terminal) => terminal.name === "Configuring...")?.dispose();
  watchOwnCode(context);
  // Update from the mounted vsix after watchOwnCode, which then offers the reload.
  childProcess.execFile("bash", [path.join(os.homedir(), ".install-claude-autoopen.sh")], () => {});
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
  } else if ((await readAgentFile(root, "tab-opened")) === undefined) {
    if (await isAgentStopped(root)) {
      await showClaudeTab(context, root);
    }
  } else if (
    (await readAgentFile(root, "handoff")) !== undefined &&
    (await readAgentFile(root, "done")) === undefined &&
    (await readAgentFile(root, "restarted")) === undefined
  ) {
    const session = await readAgentFile(root, "session");
    const sessions = getLiveSessions();
    // A reload reruns the turn only in a tab that loaded the session, which starts its process.
    if (
      sessions.some((record) => record.sessionId === session) &&
      sessions.every((record) => record.status === "idle")
    ) {
      await writeAgentFile(root, "restarted", "");
      await vscode.commands.executeCommand("workbench.action.reloadWindow");
    }
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
  let replaced;
  if (session) {
    await seedSessionMode(context, root, session);
    // Closing a tab stops the turn running in it.
    replaced = !hasBusySession() && getClaudeTab((tab) => tab.isPinned);
  }
  if (session || !getClaudeTab()) {
    await openClaudeTab(session, replaced?.group.viewColumn);
  }
  if (session && ["0", undefined].includes(await readAgentFile(root, "done"))) {
    closeAgentTerminals();
    vscode.window.terminals.find((terminal) => terminal.name === REPOS[0])?.show(true);
  }
  // editor.open reveals the tab already on the session, which can be the pinned one.
  if (replaced && !replaced.isActive) {
    await vscode.window.tabGroups.close(replaced, true);
  }
  // Pin the Claude tab so it stays at the front and isn't replaced by preview editors.
  // workbench.action.pinEditor targets the ACTIVE editor (there is no API to pin an arbitrary tab),
  // so guard on isActive to avoid pinning the wrong editor when a restored Claude tab isn't focused.
  // VS Code persists pin state across reloads, so !isPinned keeps this idempotent.
  const tab = getClaudeTab((tab) => tab.isActive && tab.group.isActive);
  if (tab) {
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
