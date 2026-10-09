#!/usr/bin/env python3
"""Run the task of a bundle with Claude Code in this terminal, in the bundle's dev container.

    agent-terminal.py BUNDLE
    agent-terminal.py BUNDLE --shell    a host shell in the bundle, for a pane next to the agent

The bundle-ctl daemon starts it in a terminal window for a queued task, so that an agent costs no
VS Code window. The container is the one VS Code would make: `up` of the devcontainer CLI that the
Dev Containers extension ships, with the arguments of that extension, on the bundle's
devcontainer.json. So the sandbox has one definition, and a window opened later attaches to it.

The claude session stays open in the terminal after its verdict (.agent/done, written by
client/agent-stop.py), for Seb to read and to type in. Quitting claude or closing the terminal
stops the container. A VS Code window opened on the bundle takes the session over instead
(client/agent-takeover.py): the terminal closes, and the container lives as long as that window.

Started on a bundle whose run already began, it goes on with that session and does not run the
task again. When another terminal holds the session, it takes it over as a window does, in the
same container.
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
from commands import (  # noqa: E402
    drop_stale_containers,
    get_devcontainer_config,
    get_worktree_bundle_folder,
)

CLI = "ms-vscode-remote.remote-containers-*/dist/spec-node/devContainersSpecCLI.js"
CONFIG_ROOT = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
GO_ON = "Continue from where you left off."
# Leave the alternate screen and the mouse modes a killed claude stays in, and show the cursor.
RESET = "\x1b[?1049l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[?25h\x1b[0m"
TAKEOVER_WAIT = 30
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
    drop_stale_containers(folder)
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
    """Run a shell of the host in the folder of the bundle, for Seb to type in."""
    folder = pathlib.Path(get_worktree_bundle_folder(bundle))
    os.chdir(folder / "odoo" if (folder / "odoo").is_dir() else folder)
    program = os.environ.get("SHELL") or "bash"
    os.execvp(program, [program])


def take_over(agent, bundle):
    """Free the session from the terminal that holds it.

    Return the container of that terminal when it still runs, and whether a turn was cut.
    """
    holder = read_text(agent / "terminal")

    def is_held():
        try:
            cmdline = pathlib.Path(f"/proc/{holder}/cmdline").read_bytes()
        except OSError:
            return False
        return bundle.encode() in cmdline.split(b"\0")

    if not is_held():
        return None, False
    container = read_text(agent / "container")
    if not container:
        raise Failure("its agent starts in another terminal")
    print(f"{bundle}: taking the session over from its other terminal", flush=True)
    subprocess.run(
        ["podman", "exec", "--user", USER, container]
        + ["python3", str(HERE / "client" / "agent-takeover.py")],
        check=False,
    )
    deadline = time.monotonic() + TAKEOVER_WAIT
    # That terminal removes the two markers at its end: write them after it.
    while is_held():
        if time.monotonic() > deadline:
            raise Failure("its other terminal still holds the session")
        time.sleep(0.2)
    cut = not (agent / "restarted").exists()
    state = subprocess.run(
        ["podman", "inspect", "--format", "{{.State.Running}}", container],
        capture_output=True,
        check=False,
        text=True,
    ).stdout.strip()
    return container if state == "true" else None, cut


def main():
    bundle = sys.argv[1]
    if sys.argv[2:] == ["--shell"]:
        shell(bundle)
        return
    agent = get_agent_folder(bundle)
    if not (agent / "task.md").is_file():
        keep_open(f"{bundle}: no task to start")
        return
    # Closing the terminal hangs up `podman exec` as well: go on to stop the container.
    signal.signal(signal.SIGHUP, lambda *args: None)
    signal.signal(signal.SIGTERM, lambda *args: sys.exit(1))
    try:
        container, cut = take_over(agent, bundle)
    except Failure as error:
        keep_open(f"{bundle}: {error}")
        return
    # Drop the handoff to a tab: left in place, this terminal would not stop its container.
    for name in ("handoff", "restarted"):
        (agent / name).unlink(missing_ok=True)
    ending = session = None
    taken_over = False
    (agent / "terminal").write_text(f"{os.getpid()}\n")
    try:
        session = read_text(agent / "session") or str(uuid.uuid4())
        (agent / "session").write_text(f"{session}\n")
        container = container or up(get_worktree_bundle_folder(bundle))
        copy_gitconfig(container)
        (agent / "container").write_text(f"{container}\n")
        code = subprocess.run(
            ["podman", "exec", "--interactive", "--tty", "--detach-keys=", "--user", USER]
            + ["--workdir", "/workspace", "--env", f"TERM={os.environ.get('TERM', 'xterm')}"]
            + [container, "bash", str(HERE / "client" / "agent-run.sh"), "--terminal", session]
            + ([GO_ON] if cut else []),
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
