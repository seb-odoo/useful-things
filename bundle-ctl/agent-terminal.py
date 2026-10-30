#!/usr/bin/env python3
"""Run the task of a bundle with Claude Code in this terminal, in the bundle's dev container.

    agent-terminal.py BUNDLE

The bundle-ctl daemon starts it in a terminal window for a queued task, so that an agent costs no
VS Code window. The container is the one VS Code would make: `up` of the devcontainer CLI that the
Dev Containers extension ships, with the arguments of that extension, on the bundle's
devcontainer.json. So the sandbox has one definition, and a window opened later attaches to it.

The claude session stays open in the terminal after its verdict (.agent/done, written by
client/agent-stop.py), for Seb to read and to type in. Quitting claude or closing the terminal
stops the container. A VS Code window opened on the bundle takes the session over instead
(client/agent-takeover.py): the terminal window closes, and the container lives
as long as that window.
A terminal closed while such a window is attached hands its session to that window the same way.
The claude runs in a pty of the wrapper, which hands its clipboard escapes (OSC 52), ignored by
terminator, to xclip while the window has the focus, and types in an idle session the task the
daemon leaves for it, the prompts and the Escape of deck, the PR events of client/prwatch.py, and
GO_ON after an API error.

Started on a bundle whose run already began, it goes on with that session and does not run the
task again. When another terminal holds the session, it takes it over as a window does, in the
same container. When a VS Code window holds it, it has that window close (.agent/close-window),
waits for the container to stop, and starts it again.

    agent-terminal.py ops-SESSION

An ops agent window runs a new Claude session in the ops container (BUNDLE_CTL_HOST_FOLDER), with
its task as first prompt when the daemon left one. On a session that has a transcript, moved from a
tab of the ops window, it resumes it in the mode of the tab, and tells it to go on when its turn
was cut, or gives it the task, the prompt of a session a crash cut. Other sessions work in that container: neither quitting claude nor closing the terminal
stops it, and a closed terminal stops its own claude.
"""

import base64
import contextlib
import fcntl
import importlib.util
import json
import os
import pathlib
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
import uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import tab_groups  # noqa: E402
from agents import (  # noqa: E402
    OPS,
    STATE,
    get_agent_folder,
    get_bundle_container,
    get_container,
    get_mtime,
    get_resume,
    get_session_status,
    get_transcript,
    has_window,
    open_unlinked,
    read_text,
    write_text,
)
from commands import (  # noqa: E402
    drop_stale_containers,
    get_devcontainer_config,
    get_worktree_bundle_folder,
)
from config import HOST_FOLDER, REPO_ROOT  # noqa: E402


def load_client(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / "client" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRWATCH = load_client("prwatch", "prwatch.py")
TITLE = load_client("title", "window-title.py")

CLAUDE = "/home/vscode/.claude/bin/claude"
CLI = "ms-vscode-remote.remote-containers-*/dist/spec-node/devContainersSpecCLI.js"
CLIPBOARD = re.compile(rb"\x1b\]52;([a-z0-9]*);([A-Za-z0-9+/=]*|\?)(?:\x07|\x1b\\)")
CLIPBOARD_LIMIT = 1 << 22
CLIPBOARD_PART = re.compile(rb"\x1b\]52;[a-z0-9]*(?:;(?:[A-Za-z0-9+/=]*|\?)\x1b?)?")
CLIPBOARD_START = b"\x1b]52;"
CONFIG_ROOT = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
DECK = STATE / "deck"
ENTER_AFTER = 0.3
EVENTS_EVERY = 0.2
GO_ON = "Continue from where you left off."
MODES = ("acceptEdits", "auto", "default", "plan")
# Leave the alternate screen and the mouse modes a killed claude stays in, and show the cursor.
RESET = "\x1b[?1049l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?2004l\x1b[?25h\x1b[0m"
NOT_TYPED = re.compile(
    rb"\x1b\[<[0-9;]*[Mm]|\x1b\[M[\s\S]{3}|\x1b\[[IO]"
    rb"|\x1b\[[?>][0-9;]*\$?[A-Za-z]|\x1b[\]P_][^\x07\x1b]*(?:\x07|\x1b\\)"
)
PASTE_END = b"\x1b[201~"
PASTE_START = b"\x1b[200~"
PROMPT_TIME = 3600
RETRY_AFTER = 5 * 60
SELECTIONS = {"c": "clipboard", "p": "primary"}
START_LOCK = pathlib.Path.home() / ".claude" / "agent-start.lock"
START_SPACE = 10
STOP_GAP = 1
STOP_TIME = 5
TAKEOVER_WAIT = 30
TOLD = STATE / "told"
TRANSCRIPTS = pathlib.Path.home() / ".claude" / "projects" / "-workspace"
USER = "vscode"
WAKE_EVERY = 2
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


def run_tty(command, waker=None):
    """Run the podman exec of a claude in a pty of its own, and copy this terminal to it and back,
    so that the clipboard escape (OSC 52) of claude, which terminator ignores, reaches the clipboard
    of the host, and that a PR event can be typed in it (Waker). Its exit code."""
    if not os.isatty(0):
        return subprocess.run(command, check=False).returncode
    size = fcntl.ioctl(0, termios.TIOCGWINSZ, bytes(8))
    pid, fd = pty.fork()
    if not pid:
        fcntl.ioctl(0, termios.TIOCSWINSZ, size)
        os.execvp(command[0], command)
    resize = signal.signal(signal.SIGWINCH, lambda *args: copy_size(fd))
    mode = termios.tcgetattr(0)
    tty.setraw(0)
    try:
        copy_tty(fd, waker)
    except BaseException:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        raise
    finally:
        signal.signal(signal.SIGWINCH, resize)
        with contextlib.suppress(OSError, termios.error):
            termios.tcsetattr(0, termios.TCSADRAIN, mode)
        os.close(fd)
    return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])


def copy_size(fd):
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, fcntl.ioctl(0, termios.TIOCGWINSZ, bytes(8)))


def copy_tty(fd, waker=None):
    """Copy the keys to the pty and its output to this terminal, until the command ends. A tail
    that may start a clipboard escape waits for the next read; a lone start of one goes out after
    50 ms with no output. The Enter of a typed PR event goes ENTER_AFTER after its text: sent
    together, claude takes them as a paste, where Enter is a new line."""
    held = b""
    enter_at = None
    inputs = [0, fd]
    while True:
        wait = 0.05 if held or enter_at else EVENTS_EVERY if waker else None
        ready = select.select(inputs, [], [], wait)[0]
        if not ready and len(held) < len(CLIPBOARD_START):
            write_terminal(held)
            held = b""
        if fd in ready:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if not data:
                write_terminal(held)
                return
            out, held = take_clipboard(held + data)
            write_terminal(out)
        if 0 in ready:
            try:
                data = os.read(0, 65536)
            except OSError:
                data = b""
            if not data:
                stop()
            if waker:
                waker.keys(data)
            with contextlib.suppress(OSError):
                write_all(fd, data)
        if enter_at and time.monotonic() >= enter_at:
            enter_at = None
            if waker.may_enter():
                with contextlib.suppress(OSError):
                    write_all(fd, b"\r")
        elif waker and not enter_at:
            escape = waker.take_stop()
            prompt = None if escape else waker.take()
            if escape:
                with contextlib.suppress(OSError):
                    write_all(fd, escape)
            elif prompt:
                with contextlib.suppress(OSError):
                    write_all(fd, prompt)
                enter_at = time.monotonic() + ENTER_AFTER


class Waker:
    """Types as the next prompt of an idle session the task the daemon left for this terminal
    (.agent/task.md with no run.lock, which it then makes), else the prompts Seb sent from deck
    (DECK/NAME/SESSION of the host, one paste), else the lines the host left in TOLD/NAME/SESSION
    and the PR events client/prwatch.py leaves in .agent/pr-events (the pr-events/ of an ops
    window is the host's), both in a busy session too, where claude shows them queued at once
    (Seb, 2026-10-27), and in one whose turn left a background command (`shell`), when nothing
    was typed in this terminal since the last Enter: a draft of Seb stays his, and a PR event
    then reaches the agent with his next prompt, by the hook of prwatch.py, which WAKE_EVERY
    after his last key leaves the time to take it. Nor after a slash command he sent, which may
    leave a menu open. Idle is no
    turn, no dialog open and no wait for the reset of the usage limit, in the claude this terminal
    started: before it, the line would go to agent-run.sh, and claude would take it with its Enter
    as a paste, left in its prompt (Seb, 2026-10-23: "composer was pre-filled"). With neither,
    GO_ON once the session has been idle RETRY_AFTER on an API error that a new request may pass,
    at most TITLE.RETRIES times in a row: the daemon then puts it back in the queue."""

    def __init__(self, agent, bundle, session, name=None):
        self.agent = agent
        self.events = agent / "pr-events"
        self.told = self.events if name else TOLD / bundle / session
        self.prompts = DECK / (name or bundle) / session
        self.bundle = bundle
        self.session = session
        self.started = time.time() * 1000
        self.line = b""
        self.draft = False
        self.looked = float("-inf")
        self.keyed = float("-inf")
        self.held = None
        self.checked = None
        self.due = False
        self.stopped = float("-inf")

    def keys(self, data):
        typed = NOT_TYPED.sub(b"", data)
        if typed:
            self.keyed = time.monotonic()
        self.line += typed
        if self.line[-1:] in (b"\r", b"\x03"):
            self.draft = self.line[-1:] == b"\r" and self.line.lstrip()[:1] == b"/"
            self.line = b""
        elif self.line:
            self.draft = True

    def take(self):
        has_events = any(map(PRWATCH.has_lines, (self.told, self.events, self.prompts)))
        now = time.monotonic()
        if now - self.stopped < STOP_GAP:
            return None
        gap = EVENTS_EVERY if has_events else WAKE_EVERY
        if self.draft or now - self.keyed < WAKE_EVERY:
            self.hold(has_events and self.describe_draft())
            return None
        if now - self.looked < gap:
            return None
        self.looked = time.monotonic()
        has_task = (self.agent / "task.md").is_file() and not (self.agent / "run.lock").exists()
        retry = not (has_task or has_events) and self.is_retry_due()
        status = get_session_status(self.bundle, self.session, self.started)
        resuming = TITLE.is_resuming(self.session)
        ready = status == "idle" or status in ("busy", "shell") and has_events
        if not (has_task or has_events or retry) or not ready or resuming:
            self.hold(has_events and f"the session is {'resuming' if resuming else status}")
            return None
        self.hold(None)
        if has_task and status == "idle":
            return self.take_task()
        if retry:
            self.due = False
            return GO_ON.encode()
        if prompts := self.take_prompts():
            return PASTE_START + "\n\n".join(prompts).encode() + PASTE_END
        told = self.take_events()
        return PASTE_START + " ".join(told).encode() + PASTE_END if told else None

    def take_stop(self):
        """Escape, Seb's key, once the daemon left `stop` for this terminal (deck's Stop) within
        STOP_TIME, while a turn runs or a dialog waits; one in STOP_TIME at most, as a second
        Escape opens the rewind of claude."""
        stop = self.prompts / "stop"
        try:
            made = stop.lstat().st_mtime
            stop.unlink()
        except OSError:
            return None
        now = time.monotonic()
        if time.time() - made > STOP_TIME or now - self.stopped < STOP_TIME:
            return None
        if get_session_status(self.bundle, self.session, self.started) not in ("busy", "waiting"):
            return None
        self.stopped = now
        return b"\x1b"

    def take_prompts(self):
        """The prompts deck left for this session, oldest first, each once. One older than
        PROMPT_TIME is dropped, as the session it waited for may resume days later, and so is one
        with a control character, which would end the paste and type keys."""
        try:
            names = sorted(filter(PRWATCH.is_line, os.listdir(self.prompts)))
        except OSError:
            return []
        prompts = []
        for name in names:
            try:
                text = (self.prompts / name).read_text()
                (self.prompts / name).unlink()
            except (OSError, ValueError):
                continue
            stem = name.removesuffix(".txt")
            young = stem.isdigit() and time.time_ns() - int(stem) < PROMPT_TIME * 10**9
            if young and not CONTROL.search(text):
                prompts.append(text)
        return prompts

    def take_events(self):
        """The lines of the host, then the PR events, typed as one paste: as keys, a line typed
        just as a new claude goes idle stays in its prompt, each Enter a new line (2026-10-29). One
        with a control key (an escape that ends the paste, a tab) or no letter first (a slash
        command, the shell of claude) is dropped, and noted with its keys escaped. The bundle
        writes its pr-events/: a line there that client/prwatch.py did not write is dropped too,
        as its own text would be typed as Seb's."""
        told = []
        for line in PRWATCH.take(self.told, self.note):
            if line[:1].isalpha() and not CONTROL.search(line) and "\t" not in line:
                told.append(line)
            else:
                self.note(f"dropped a line that would type keys: {line[:100]!r}")
        for line in PRWATCH.take(self.events) if self.events != self.told else ():
            if PRWATCH.is_told(line):
                told.append(line)
            else:
                self.note(f"dropped a line that prwatch.py does not write: {line[:100]!r}")
        return told

    def may_enter(self):
        """Whether the Enter of a typed prompt may go: on a dialog claude opened since the text, it
        would pick the first answer, mostly a yes."""
        if get_session_status(self.bundle, self.session, self.started) != "waiting":
            return True
        self.note("an Enter not sent: a dialog opened after the text")
        return False

    def describe_draft(self):
        """What Seb typed since his last Enter, counted, never its text."""
        if not self.draft:
            return "a key of Seb within 2 s"
        if not self.line:
            return "a slash command of Seb"
        printable = sum(32 <= byte < 127 for byte in self.line)
        escapes, erased = self.line.count(b"\x1b"), self.line.count(b"\x7f")
        return f"a draft of Seb: {printable} printable, {escapes} escapes, {erased} backspaces"

    def hold(self, why):
        """Note in prwatch.log why a PR event waits, once per reason: the counts of a draft that
        grows are not a new one."""
        reason = why and why.partition(":")[0]
        if reason and reason != self.held:
            self.note(f"an event waits: {why}")
        self.held = reason or None

    def note(self, text):
        log = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        with (
            contextlib.suppress(OSError),
            open(open_unlinked(self.agent / "prwatch.log", log), "a") as file,
        ):
            file.write(f"{time.strftime('%H:%M:%S')} {text}\n")

    def is_retry_due(self):
        """The transcript is read once it has not changed for RETRY_AFTER, and once per change: a
        retry it did not take waits for the next one."""
        transcript = get_transcript(self.session)
        stamp = transcript and get_mtime(transcript)
        if not stamp or time.time() - stamp < RETRY_AFTER:
            return False
        if stamp != self.checked:
            self.checked = stamp
            self.due = 0 < TITLE.count_api_errors(self.session) <= TITLE.RETRIES
        return self.due

    def take_task(self):
        task = read_text(self.agent / "task.md")
        if task is None:
            return None
        try:
            (self.agent / "run.lock").mkdir()
        except FileExistsError:
            return None
        return PASTE_START + task.encode() + PASTE_END


def write_terminal(data):
    try:
        write_all(1, data)
    except OSError:
        stop()


def write_all(fd, data):
    while data:
        data = data[os.write(fd, data) :]


def take_clipboard(data):
    """The output without its clipboard escapes, each set on the host, and the tail held for the
    next read: an escape not ended yet, or what may start one. A tail that cannot be one goes out,
    as a program may print the start of an escape (a cat of a binary file)."""
    out = CLIPBOARD.sub(lambda match: set_clipboard(*match.groups()), data)
    start = out.rfind(CLIPBOARD_START)
    if start >= 0 and len(out) - start < CLIPBOARD_LIMIT and CLIPBOARD_PART.fullmatch(out, start):
        return out[:start], out[start:]
    for size in range(len(CLIPBOARD_START) - 1, 0, -1):
        if out.endswith(CLIPBOARD_START[:size]):
            return out[:-size], out[-size:]
    return out, b""


def set_clipboard(targets, payload):
    """Set the text of a clipboard escape in the selections it names, never answer the query of
    one (its payload is ?). Done aside, as xclip waits for the X server."""
    try:
        text = base64.b64decode(payload, validate=True)
    except ValueError:
        return b""
    targets = targets.decode() or "c"
    selections = [SELECTIONS[target] for target in targets if target in SELECTIONS]
    threading.Thread(target=write_clipboard, args=(text, selections), daemon=True).start()
    return b""


def write_clipboard(text, selections):
    """xclip, in a session of its own, as it stays to serve the selection after the window closes.
    Only while the window of this terminal has the focus, its terminator being the parent of this
    process: else a process of the container could fill the clipboard behind Seb's back."""
    if not is_focused():
        return
    for selection in selections:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["xclip", "-selection", selection],
                check=False,
                input=text,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )


def is_focused():
    try:
        window = get_property("-root", "_NET_ACTIVE_WINDOW")
        return get_property("-id", window, "_NET_WM_PID") == str(os.getppid())
    except (IndexError, OSError, subprocess.SubprocessError):
        return False


def get_property(*args):
    """The value of an X property: the last word xprop prints."""
    out = subprocess.run(["xprop", *args], capture_output=True, check=False, text=True, timeout=2)
    return out.stdout.split()[-1]


def stop(*args):
    """End on a closed window or a kill: the finally of main stops the container or hands the
    session over, and a second signal must not cut that."""
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise HangUp(1)


def close_groups(agent, bundle):
    """Close the Firefox tab groups of the bundle, as Seb closed its agent. Not when the daemon
    closed the window to queue the agent again (its `pause` stays until the window is gone), nor
    when the host shuts down, as Firefox opens the groups again at its start."""
    if (agent / "pause").exists():
        return
    state = subprocess.run(
        ["systemctl", "is-system-running"],
        capture_output=True,
        check=False,
        text=True,
    ).stdout.strip()
    if state != "stopping":
        tab_groups.ask({"op": "close", "bundle": bundle})


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
    # The bundle writes .agent/container: only whether the file exists is used, never its value.
    container = (agent / "container").exists() and get_bundle_container(bundle)
    if not container:
        raise Failure("its agent starts in another terminal")
    why = os.environ.get("BUNDLE_CTL_TAKEOVER") or "taking the session over from its other terminal"
    print(f"{bundle}: {why}", flush=True)
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
    write_text(agent / "close-window", "")
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
        write_text(agent / "mode", mode)
    return container, cut


def get_ops_start(session, task):
    """The claude options of an ops window: a new session on its task, or the session of a tab
    moved here, in the mode of the tab, told to go on when its turn was cut, or a cut session on
    the task that resumes it."""
    transcript = get_transcript(session)
    if not transcript:
        start = ["--session-id", session, "--permission-mode", "auto"]
        return start + (["--", task] if task else [])
    mode, cut = get_resume(transcript)
    start = ["--resume", session, "--permission-mode", mode if mode in MODES else "auto"]
    return start + (["--", task or GO_ON] if task or cut else [])


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
    claude = [CLAUDE, "--model", "opus", "--plugin-dir", str(HERE / "agentbar")]
    claude += get_ops_start(session, task)
    container = None
    try:
        if not HOST_FOLDER:
            raise Failure("no ops container: BUNDLE_CTL_HOST_FOLDER is not set")
        container = get_container(HOST_FOLDER) or up(HOST_FOLDER)
        copy_gitconfig(container)
        space_start()
        code = run_tty(
            ["podman", "exec", "--interactive", "--tty", "--detach-keys=", "--user", USER]
            + ["--workdir", REPO_ROOT, "--env", f"TERM={os.environ.get('TERM', 'xterm')}"]
            + ["--env", "SHELL=/bin/bash", "--env", f"AGENT_WINDOW={name}", container, *claude],
            Waker(agent, os.path.basename(HOST_FOLDER), session, name),
        )
    except HangUp:
        # podman exec does not pass the hang-up on: the claude of the container still runs.
        if container:
            # Its parent is 0, as podman exec started it: a tab may hold the session too. The
            # match ends at the session id, as the task that follows may name another session.
            # CLAUDE execs the binary of the extension, so the process shows that path instead.
            own = " ".join(claude[1 : claude.index(session) + 1])
            stop_claude = ["pkill", "--parent", "0", "-f", f"^[^ ]*/claude {own} "]
            subprocess.run(["podman", "exec", "--user", USER, container, *stop_claude], check=False)
        raise
    except Failure as error:
        agent.mkdir(parents=True, exist_ok=True)
        (agent / "done").write_text("failed\n")
        keep_open(f"ops: {error}")
        return
    if code:
        keep_open(f"ops: claude ended with code {code}")


def attach_remote(bundle):
    """The agent runs on a worker: this window shows its claude, through its stand-in, and
    closing it leaves the agent running there (README.md, "Remote agents").
    """
    code = run_tty(
        ["podman", "exec", "--interactive", "--tty", "--detach-keys=",
         "--env", f"TERM={os.environ.get('TERM', 'xterm')}", f"rrs-{bundle}",
         "python3", "-I", "/opt/remote-runs/standin/standin.py", "attach"],
    )
    if code:
        keep_open(f"{bundle}: its agent on the worker ended with code {code}")


def main():
    bundle = sys.argv[1]
    if bundle.startswith(OPS):
        run_ops(bundle)
        return
    agent = get_agent_folder(bundle)
    if not (agent / "task.md").is_file():
        keep_open(f"{bundle}: no task to start")
        return
    if (agent / "remote.json").exists():
        attach_remote(bundle)
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
    write_text(agent / "terminal", f"{os.getpid()}\n")
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
        write_text(agent / "session", f"{session}\n")
        container = container or up(get_worktree_bundle_folder(bundle))
        copy_gitconfig(container)
        write_text(agent / "container", f"{container}\n")
        code = run_tty(
            ["podman", "exec", "--interactive", "--tty", "--detach-keys=", "--user", USER]
            + ["--workdir", "/workspace", "--env", f"TERM={os.environ.get('TERM', 'xterm')}"]
            + ["--env", f"AGENT_WINDOW={bundle}"]
            + [container, "bash", str(HERE / "client" / "agent-run.sh"), "--terminal", session]
            + ([GO_ON] if cut else []),
            Waker(agent, bundle, session),
        )
        if code:
            ending = f"claude ended with code {code}"
    except HangUp:
        hung_up = True
        raise
    except Failure as error:
        ending = str(error)
        write_text(agent / "result.md", f"{ending}\n")
        write_text(agent / "done", "failed\n")
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
        if not taken_over and not ending:
            close_groups(agent, bundle)
    if ending and not taken_over:
        keep_open(f"{bundle}: {ending}", session)
    if handed and os.environ.get("BUNDLE_CTL_WINDOW_PID"):
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(os.environ["BUNDLE_CTL_WINDOW_PID"]), signal.SIGUSR1)


if __name__ == "__main__":
    main()
