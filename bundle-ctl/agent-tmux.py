#!/usr/bin/env python3
"""Run the agent of a bundle in a tmux server of its own, with no window.

    agent-tmux.py BUNDLE COMMAND...

The bundle-ctl daemon starts it with COMMAND, the one that runs the agent (agent-terminal.py
BUNDLE). No agent opens a desktop window (Seb, 2026-11-25, Deck task c6824190): Seb follows and
answers it in Deck. The server listens on tmux/HASH.PID under XDG_RUNTIME_DIR/bundle-ctl
(agents.get_tmux_socket), one per process, as a takeover starts a second one while the first still
runs. It holds one session, named after the bundle, with COMMAND alone, and ends with it. `pfb -t`,
`ocode -t` and the Terminal button of Deck attach a terminal to it (window-picker.py).

This process stands for the window of the agent: the agent list, the rofi list and Deck show a
row for it (window-picker.py, `tmux:PID`), and it keeps what that row needs in tmux/HASH.PID.json
(agents.get_holder_file): the title a window had, the pid of the pane, and whether a terminal is
attached. The title says what the work is about (client/window-title.py) and follows the status
of the agent's session, in the sessions folder of the bundle's container: it says the agent
starts until its claude does, and that it failed when the run ends before (.agent/done written
since it started). An agent on a worker takes the record that the worker mirrors back of a claude
started since its run (.agent/run.lock). A busy session is read again every REFRESH seconds, as a
call that waits for Seb in hok changes its mark and not its status. Once its claude ends, the last
mark stays. An ops agent (ops-SESSION) follows the record of that session in the sessions folder
of the ops container, and the title of its conversation; its mark goes once its claude ended.

When a VS Code window or another terminal takes the session over, agent-terminal.py ends this
server: SIGUSR1 to BUNDLE_CTL_WINDOW_PID, this process, which the server's environment holds. The
daemon and the agent list close it the same way, and so does a tab of the ops VS Code window that
resumes the session of an ops agent, as two claude processes on one session would each go on
from where they loaded it. A pane split by hand is a host shell: the host .bashrc says so in any
shell that holds BUNDLE_CTL_AGENT_WINDOW, the bundle name.

While it runs, it keeps a note in windows/BUNDLE.json, beside the state of the daemon, and drops it
when someone closes it (the daemon, the agent list, a takeover, a tab that takes the session), its
claude ends or its tmux does not start. A shutdown kills it and leaves the note, and the daemon
brings the agent back (README.md, "After a shutdown").
"""

import contextlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from agents import (  # noqa: E402
    MAX_RECORD,
    OPS,
    drop_window_note,
    get_agent_folder,
    get_holder_file,
    get_mtime,
    get_tmux_socket,
    is_agent_running,
    is_host_stopping,
    read_bytes,
    read_text,
    write_window_note,
)
from config import CLAUDE_CONFIG_CONTAINER, HOST_FOLDER  # noqa: E402

ATTACH_CHECK = 3
CHECK = 1
CLAUDE_CONFIG = pathlib.Path(CLAUDE_CONFIG_CONTAINER)
COMMAND = "BUNDLE_CTL_AGENT_COMMAND"
CONFIG = HERE / "tmux.conf"
REFRESH = 10
SIZE = ("-x", "200", "-y", "50")
TITLE = HERE / "client" / "window-title.py"
TMUX = "tmux"
WORKER = re.compile(r"[a-z][a-z0-9-]{0,39}")

closing = []
state = {}
titles = {}


def tmux(socket, *args, **kwargs):
    return subprocess.run([TMUX, "-S", str(socket), *args], check=False, **kwargs)


def load_title():
    """client/window-title.py, loaded again once it changed, as an agent runs for days: a python3
    that ran it took 0.1 s of CPU, every REFRESH seconds for each ops or busy agent."""
    stamp = get_mtime(TITLE)
    if titles.get("stamp") != stamp:
        spec = importlib.util.spec_from_file_location("title", TITLE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        titles.update(stamp=stamp, module=module)
    return titles["module"]


def get_title(bundle, *statuses):
    try:
        worker = json.loads(read_text(get_agent_folder(bundle) / "remote.json"))["worker"]
    except (TypeError, ValueError, KeyError):
        worker = None
    where = worker if type(worker) is str and WORKER.fullmatch(worker) else None
    try:
        title = load_title()
        if statuses and statuses[0] in title.FLAGS:
            return title.get_title(bundle, flag=statuses[0], where=where) or bundle
        pairs = dict(status.split("=", 1) for status in statuses)
        return title.get_title(bundle, pairs, where=where) or bundle
    except Exception:  # noqa: BLE001
        return bundle


def get_record(bundle, since):
    """The record of the newest claude of the agent, in the sessions folder of its container:
    client/agent-run.sh names it after the bundle, and the claude of an ops agent has the session
    of its name."""
    session = bundle.removeprefix(OPS) if bundle.startswith(OPS) else None
    container = os.path.basename(HOST_FOLDER or "") if session else bundle
    key, value = ("sessionId", session) if session else ("name", bundle)
    records = []
    for path in (CLAUDE_CONFIG / container / "sessions").glob("*.json"):
        data = read_bytes(path, MAX_RECORD) or b""
        try:
            record = json.loads(data) if value.encode() in data else {}
        except ValueError:
            continue
        started = record.get("startedAt", 0) if isinstance(record, dict) else None
        if type(started) not in (int, float) or not isinstance(record.get("sessionId"), str):
            continue
        if record.get(key) == value and started >= since:
            records.append(record)
    return max(records, key=lambda record: record["startedAt"], default=None)


def show(**changes):
    """Keep what the row of the agent needs in its holder file, written whole. A write that fails
    leaves the row as it was: the agent goes on."""
    state.update(changes)
    path = state["path"]
    fresh = path.with_name(f".{path.name}.new")
    with contextlib.suppress(OSError):
        fresh.write_text(json.dumps({key: value for key, value in state.items() if key != "path"}))
        fresh.replace(path)


def is_attached(socket):
    return bool(tmux(socket, "list-clients", capture_output=True, text=True).stdout.strip())


def close(socket):
    closing.append(True)
    tmux(socket, "kill-server", capture_output=True)


def is_closed_on_purpose():
    """Whether someone closed the agent (the daemon, the agent list, a takeover, a tab that took
    the session), its claude ended or its tmux did not start, while the host does not shut down."""
    return bool(closing) or not is_host_stopping()


def is_alive(pid):
    """Whether a process runs: a zombie no longer does."""
    try:
        return pathlib.Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def follow(bundle, socket, pane, since):
    """Follow the session of the agent in its title until the command of the pane ends, and end
    the server when a VS Code tab takes the session (its record has the entrypoint claude-vscode)."""
    signal.signal(signal.SIGUSR1, lambda *args: close(socket))
    agent = get_agent_folder(bundle)
    done = agent / "done"
    run = get_mtime(agent / "run.lock") if (agent / "remote.json").exists() else None
    start = since if run is None else min(since, run * 1000)
    ended = False
    refreshed = checked = time.monotonic()
    shown = None
    while pane and is_alive(pane):
        time.sleep(CHECK)
        if time.monotonic() - checked >= ATTACH_CHECK:
            checked = time.monotonic()
            if (attached := is_attached(socket)) != state.get("attached"):
                show(attached=attached)
        record = get_record(bundle, start)
        if record and record.get("entrypoint") == "claude-vscode":
            if not closing:
                close(socket)
            continue
        if bundle.startswith(OPS) and time.monotonic() - refreshed >= REFRESH:
            refreshed, shown = time.monotonic(), None
            ended = ended or (bool(record) and not is_agent_running(bundle))
        elif record and record.get("status") == "busy" and time.monotonic() - refreshed >= REFRESH:
            refreshed, shown = time.monotonic(), None
        status = record and f"{record['sessionId']}={record.get('status') or ''}"
        if not status and not shown and (get_mtime(done) or 0) * 1000 >= since:
            status = "--failed"
        if not status or status == shown:
            continue
        title = get_title(bundle) if ended else get_title(bundle, status)
        if title != state.get("title"):
            show(title=title)
        shown = status


def start(bundle, socket, command):
    """Start the server with COMMAND in its one session, the pid of that command, None when it
    already ended. COMMAND goes through the environment, as the process scans take a command line
    that holds agent-terminal.py BUNDLE for the agent itself."""
    socket.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ[COMMAND] = json.dumps(command)
    pane = [sys.executable, str(pathlib.Path(__file__).resolve())]
    name = re.sub(r"[.:]", "_", bundle)
    started = tmux(socket, "-f", str(CONFIG), "new-session", "-d", "-s", name, *SIZE, *pane)
    del os.environ[COMMAND]
    if started.returncode:
        raise OSError(f"tmux exited with {started.returncode}")
    out = tmux(socket, "display-message", "-p", "#{pane_pid}", capture_output=True, text=True)
    return int(out.stdout) if out.stdout.strip().isdigit() else None


def main():
    if len(sys.argv) == 1 and COMMAND in os.environ:
        command = json.loads(os.environ.pop(COMMAND))
        os.execvp(command[0], command)
    bundle, *command = sys.argv[1:]
    socket = get_tmux_socket(bundle, os.getpid())
    os.environ["BUNDLE_CTL_AGENT_WINDOW"] = bundle
    os.environ["BUNDLE_CTL_WINDOW_PID"] = str(os.getpid())
    since = time.time() * 1000
    write_window_note(bundle, os.getpid())
    state.update(path=get_holder_file(bundle, os.getpid()), name=bundle, attached=False)
    try:
        pane = start(bundle, socket, command)
    except OSError as error:
        print(f"{bundle}: {error}", file=sys.stderr)
        closing.append(True)
    else:
        show(pane=pane, title=get_title(bundle, "--starting"))
        follow(bundle, socket, pane, since)
    with contextlib.suppress(OSError):
        state["path"].unlink()
    if is_closed_on_purpose():
        drop_window_note(bundle, os.getpid())
    if bundle.startswith(OPS):
        shutil.rmtree(get_agent_folder(bundle), ignore_errors=True)


if __name__ == "__main__":
    main()
