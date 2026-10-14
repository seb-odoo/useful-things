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
(client/agent-takeover.py): the terminal window closes, its shell too, and the container lives
as long as that window.
A terminal closed while such a window is attached hands its session to that window the same way.

Started on a bundle whose run already began, it goes on with that session and does not run the
task again. When another terminal holds the session, it takes it over as a window does, in the
same container. When a VS Code window holds it, it has that window close (.agent/close-window),
waits for the container to stop, and starts it again.

    agent-terminal.py ops-SESSION
    agent-terminal.py ops-SESSION --shell    a host shell in the repos

An ops agent window runs a new Claude session in the ops container (BUNDLE_CTL_HOST_FOLDER), with
its task as first prompt when the daemon left one. On a session that has a transcript, moved from a
tab of the ops window, it resumes it in the mode of the tab, and tells it to go on when its turn
was cut. Other sessions work in that container: neither quitting claude nor closing the terminal
stops it, and a closed terminal stops its own claude.
"""

import contextlib
import fcntl
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from agents import (  # noqa: E402
    OPS,
    get_agent_folder,
    get_bundle_container,
    get_container,
    get_resume,
    get_transcript,
    has_window,
    read_text,
)
from commands import (  # noqa: E402
    drop_stale_containers,
    get_devcontainer_config,
    get_worktree_bundle_folder,
)
from config import HOST_FOLDER, REPO_ROOT  # noqa: E402

CLAUDE = "/home/vscode/.claude/bin/claude"
CLI = "ms-vscode-remote.remote-containers-*/dist/spec-node/devContainersSpecCLI.js"
CONFIG_ROOT = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
GO_ON = "Continue from where you left off."
MODES = ("acceptEdits", "auto", "default", "plan")
# Leave the alternate screen and the mouse modes a killed claude stays in, and show the cursor.
RESET = "\x1b[?1049l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[?25h\x1b[0m"
START_LOCK = pathlib.Path.home() / ".claude" / "agent-start.lock"
START_SPACE = 10
TAKEOVER_WAIT = 30
TRANSCRIPTS = pathlib.Path.home() / ".claude" / "projects" / "-workspace"
USER = "vscode"
WINDOW_WAIT = 120


class Failure(Exception):
    pass


class HangUp(SystemExit):
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
    """Run a shell of the host in the folder of the bundle, or in the repos for an ops window, for
    Seb to type in."""
    if bundle.startswith(OPS):
        os.chdir(REPO_ROOT)
    else:
        folder = pathlib.Path(get_worktree_bundle_folder(bundle))
        os.chdir(folder / "odoo" if (folder / "odoo").is_dir() else folder)
    program = os.environ.get("SHELL") or "bash"
    os.execvp(program, [program])


def stop(*args):
    """End on a closed window or a kill: the finally of main stops the container or hands the
    session over, and a second signal must not cut that."""
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise HangUp(1)


def hand_over(container):
    """Stop the claude of the session in the container, where the tab of a window can go on."""
    subprocess.run(
        ["podman", "exec", "--user", USER, container]
        + ["python3", str(HERE / "client" / "agent-takeover.py")],
        check=False,
    )


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
    hand_over(container)
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


def leave_window(agent, bundle):
    """Have the VS Code window that holds the session close, and wait for its container to stop.

    Return the container when it still runs with no window, and whether a turn was cut.
    """
    container = get_bundle_container(bundle)
    if not container or not has_window(container):
        return None, False
    print(f"{bundle}: waiting for its VS Code window to close", flush=True)
    (agent / "close-window").touch()
    deadline = time.monotonic() + WINDOW_WAIT
    try:
        while container := get_bundle_container(bundle):
            if time.monotonic() > deadline:
                if has_window(container):
                    raise Failure("its VS Code window stayed open, the session goes on there")
                break
            time.sleep(1)
    finally:
        (agent / "close-window").unlink(missing_ok=True)
    # A window opened later opens its tab on the session again.
    (agent / "tab-opened").unlink(missing_ok=True)
    mode, cut = get_resume(TRANSCRIPTS / f"{read_text(agent / 'session')}.jsonl")
    if mode:
        (agent / "mode").write_text(mode)
    return container, cut


def get_ops_start(session, task):
    """The claude options of an ops window: a new session on its task, or the session of a tab
    moved here, in the mode of the tab, told to go on when its turn was cut."""
    transcript = get_transcript(session)
    if not transcript:
        start = ["--session-id", session, "--permission-mode", "auto"]
        return start + (["--", task] if task else [])
    mode, cut = get_resume(transcript)
    start = ["--resume", session, "--permission-mode", mode if mode in MODES else "auto"]
    return start + (["--", GO_ON] if cut else [])


def space_start():
    """Start no claude within START_SPACE seconds of another, as client/agent-run.sh: every
    container shares ~/.claude, and claude processes started together fail each other's token
    refresh."""
    lock = START_LOCK.open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    timer = threading.Timer(START_SPACE, lock.close)
    timer.daemon = True
    timer.start()


def run_ops(name):
    session = name.removeprefix(OPS)
    agent = get_agent_folder(name)
    task = read_text(agent / "task.md")
    (agent / "task.md").unlink(missing_ok=True)
    signal.signal(signal.SIGHUP, stop)
    signal.signal(signal.SIGTERM, stop)
    container = None
    try:
        if not HOST_FOLDER:
            raise Failure("no ops container: BUNDLE_CTL_HOST_FOLDER is not set")
        container = get_container(HOST_FOLDER) or up(HOST_FOLDER)
        copy_gitconfig(container)
        space_start()
        code = subprocess.run(
            ["podman", "exec", "--interactive", "--tty", "--detach-keys=", "--user", USER]
            + ["--workdir", REPO_ROOT, "--env", f"TERM={os.environ.get('TERM', 'xterm')}"]
            + ["--env", "SHELL=/bin/bash", container, CLAUDE, "--model", "opus"]
            + get_ops_start(session, task),
            check=False,
        ).returncode
    except HangUp:
        # podman exec does not pass the hang-up on: the claude of the container still runs.
        if container:
            # Its parent is 0, as podman exec started it: a tab may hold the session too.
            stop_claude = ["pkill", "--parent", "0", "-f", session]
            subprocess.run(["podman", "exec", "--user", USER, container, *stop_claude], check=False)
        raise
    except Failure as error:
        agent.mkdir(parents=True, exist_ok=True)
        (agent / "done").write_text("failed\n")
        keep_open(f"ops: {error}")
        return
    if code:
        keep_open(f"ops: claude ended with code {code}")


def main():
    bundle = sys.argv[1]
    if sys.argv[2:] == ["--shell"]:
        shell(bundle)
        return
    if bundle.startswith(OPS):
        run_ops(bundle)
        return
    agent = get_agent_folder(bundle)
    if not (agent / "task.md").is_file():
        keep_open(f"{bundle}: no task to start")
        return
    signal.signal(signal.SIGHUP, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        container, cut = take_over(agent, bundle)
    except Failure as error:
        keep_open(f"{bundle}: {error}")
        return
    # Drop the handoff to a tab: left in place, this terminal would not stop its container.
    for name in ("handoff", "restarted"):
        (agent / name).unlink(missing_ok=True)
    ending = session = None
    handed = hung_up = taken_over = False
    (agent / "terminal").write_text(f"{os.getpid()}\n")
    try:
        if not container:
            container, window_cut = leave_window(agent, bundle)
            cut = cut or window_cut
    except HangUp:
        (agent / "terminal").unlink(missing_ok=True)
        raise
    except Failure as error:
        (agent / "terminal").unlink(missing_ok=True)
        keep_open(f"{bundle}: {error}")
        return
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
    except HangUp:
        hung_up = True
        raise
    except Failure as error:
        ending = str(error)
        (agent / "result.md").write_text(f"{ending}\n")
        (agent / "done").write_text("failed\n")
    finally:
        container = container or get_bundle_container(bundle)
        window = bool(container) and has_window(container)
        # podman exec does not pass the hang-up on: the claude of the container still runs.
        if hung_up and window and not (agent / "handoff").exists():
            hand_over(container)
        (agent / "container").unlink(missing_ok=True)
        (agent / "terminal").unlink(missing_ok=True)
        handed = (agent / "handoff").exists()
        taken_over = handed or window
        if container and not taken_over:
            subprocess.run(["podman", "stop", container], check=False, capture_output=True)
    if ending and not taken_over:
        keep_open(f"{bundle}: {ending}", session)
    if handed and os.environ.get("BUNDLE_CTL_WINDOW_PID"):
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(os.environ["BUNDLE_CTL_WINDOW_PID"]), signal.SIGUSR1)


if __name__ == "__main__":
    main()
