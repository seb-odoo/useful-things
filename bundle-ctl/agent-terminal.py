#!/usr/bin/env python3
"""Run the task of a bundle with Claude Code in this terminal, in the bundle's dev container.

    agent-terminal.py BUNDLE
    agent-terminal.py BUNDLE --shell    a shell in that container, for a pane next to the agent

The bundle-ctl daemon starts it in a terminal window for a queued task, so that an agent costs no
VS Code window. The container is the one VS Code would make: `up` of the devcontainer CLI that the
Dev Containers extension ships, with the arguments of that extension, on the bundle's
devcontainer.json. So the sandbox has one definition, and a window opened later attaches to it.

The claude session stays open in the terminal after its verdict (.agent/done, written by
client/agent-stop.py), for Seb to read and to type in. Quitting claude or closing the terminal
stops the container. A VS Code window opened on the bundle takes the session over instead
(client/agent-takeover.py): the terminal closes, and the container lives as long as that window.
"""

import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from agents import get_agent_folder, has_window, read_text  # noqa: E402
from commands import get_devcontainer_config, get_worktree_bundle_folder  # noqa: E402

CLI = "ms-vscode-remote.remote-containers-*/dist/spec-node/devContainersSpecCLI.js"
CONFIG_ROOT = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
# Leave the alternate screen and the mouse modes a killed claude stays in, and show the cursor.
RESET = "\x1b[?1049l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[?25h\x1b[0m"
SHELL_WAIT = 60
TRANSCRIPTS = pathlib.Path.home() / ".claude" / "projects" / "-workspace"
USER = "vscode"


class Failure(Exception):
    pass


def get_cli():
    node = shutil.which("node")
    found = (pathlib.Path.home() / ".vscode" / "extensions").glob(CLI)
    cli = max(found, key=lambda path: [int(n) for n in re.findall(r"\d+", str(path))], default=None)
    if not node or not cli:
        raise Failure("the devcontainer CLI needs node and the Dev Containers extension of VS Code")
    return [node, str(cli)]


def up(folder):
    config = get_devcontainer_config(folder)
    data = CONFIG_ROOT / "Code/User/globalStorage/ms-vscode-remote.remote-containers/data"
    res = subprocess.run(
        [
            *get_cli(),
            "up",
            "--user-data-folder",
            str(data),
            "--docker-path",
            str(HERE.parent / "devcontainer" / "vscode-podman.sh"),
            "--docker-compose-path",
            "podman-compose",
            "--workspace-folder",
            folder,
            "--workspace-mount-consistency",
            "cached",
            "--id-label",
            f"devcontainer.local_folder={folder}",
            "--id-label",
            f"devcontainer.config_file={config}",
            "--config",
            config,
            "--default-user-env-probe",
            "loginInteractiveShell",
            "--mount",
            "type=volume,source=vscode,target=/vscode,external=true",
            "--skip-post-create",
            "--update-remote-user-uid-default",
            "on",
            "--mount-workspace-git-root",
        ],
        check=False,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        result = json.loads(res.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        result = {}
    if result.get("outcome") != "success":
        raise Failure(f"devcontainer up failed: {result.get('message') or res.stdout.strip()}")
    return result["containerId"]


def copy_gitconfig(container):
    """As VS Code does when it attaches: git has no identity in the container without it."""
    gitconfig = pathlib.Path.home() / ".gitconfig"
    if gitconfig.is_file():
        subprocess.run(
            ["podman", "exec", "--interactive", "--user", USER, container]
            + ["sh", "-c", '[ -e "$HOME/.gitconfig" ] || cat >"$HOME/.gitconfig"'],
            check=False,
            input=gitconfig.read_bytes(),
        )


def keep_open(ending, session=None):
    """Show why no claude runs here, with what the agent ran, and wait for Enter in a shell.

    The claude of a terminal draws on the alternate screen, which empties when claude ends.
    """
    transcript = TRANSCRIPTS / f"{session}.jsonl"
    try:
        sys.stdout.write(RESET)
        sys.stdout.flush()
        subprocess.run(["stty", "sane"], check=False)
        if session and transcript.is_file():
            with transcript.open() as file, tempfile.TemporaryDirectory() as folder:
                subprocess.run(
                    [sys.executable, str(HERE / "client" / "stream-format.py"), f"{folder}/result"],
                    check=False,
                    stdin=file,
                )
    except OSError:
        return
    wait = 'printf "\\033]0;%s\\007\\n%s\\nPress Enter to close.\\n" "$0" "$0"; read -r _'
    os.execvp("sh", ["sh", "-c", wait, ending])


def shell(bundle):
    """Run a shell in the container of the bundle's agent, once it is up."""
    agent = get_agent_folder(bundle)
    deadline = time.monotonic() + SHELL_WAIT
    waiting = False
    while not (container := read_text(agent / "container")):
        # Both panes start together: the agent may not have written .agent/terminal yet.
        if not (agent / "terminal").exists() and time.monotonic() > deadline:
            return
        if not waiting:
            print(f"{bundle}: waiting for the container", flush=True)
            waiting = True
        time.sleep(1)
    has_odoo = os.path.isdir(f"{get_worktree_bundle_folder(bundle)}/odoo")
    link = f"\x1b]8;;odoo-bundle://{bundle}\x1b\\ocode\x1b]8;;\x1b\\"
    print(f"{bundle}: {link} opens its VS Code window", flush=True)
    os.execvp(
        "podman",
        ["podman", "exec", "--interactive", "--tty", "--detach-keys=", "--user", USER]
        + ["--workdir", "/workspace/odoo" if has_odoo else "/workspace"]
        + ["--env", f"TERM={os.environ.get('TERM', 'xterm')}", container, "bash"],
    )


def main():
    bundle = sys.argv[1]
    if sys.argv[2:] == ["--shell"]:
        shell(bundle)
        return
    agent = get_agent_folder(bundle)
    if not (agent / "task.md").is_file() or (agent / "run.lock").exists():
        keep_open(f"{bundle}: no task to start")
        return
    # Closing the terminal hangs up `podman exec` as well: go on to stop the container.
    signal.signal(signal.SIGHUP, lambda *args: None)
    signal.signal(signal.SIGTERM, lambda *args: sys.exit(1))
    container = ending = session = None
    taken_over = False
    (agent / "terminal").write_text(f"{os.getpid()}\n")
    try:
        session = read_text(agent / "session") or str(uuid.uuid4())
        (agent / "session").write_text(f"{session}\n")
        container = up(get_worktree_bundle_folder(bundle))
        copy_gitconfig(container)
        (agent / "container").write_text(f"{container}\n")
        code = subprocess.run(
            ["podman", "exec", "--interactive", "--tty", "--detach-keys=", "--user", USER]
            + ["--workdir", "/workspace", "--env", f"TERM={os.environ.get('TERM', 'xterm')}"]
            + [container, "bash", str(HERE / "client" / "agent-run.sh"), "--terminal", session],
            check=False,
        ).returncode
        if code:
            ending = f"claude ended with code {code}"
    except Failure as error:
        ending = str(error)
        (agent / "result.md").write_text(f"{ending}\n")
        (agent / "done").write_text("failed\n")
    finally:
        (agent / "container").unlink(missing_ok=True)
        (agent / "terminal").unlink(missing_ok=True)
        taken_over = (agent / "handoff").exists() or (container and has_window(container))
        if container and not taken_over:
            subprocess.run(["podman", "stop", container], check=False, capture_output=True)
    if ending and not taken_over:
        keep_open(f"{bundle}: {ending}", session)


if __name__ == "__main__":
    main()
