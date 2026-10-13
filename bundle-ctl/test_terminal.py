#!/usr/bin/env python3
"""What agent-terminal.py does when its claude or its terminal ends, and when a VS Code window
holds its session, with a fake podman and the wrapper in a pty: python3 test_terminal.py
"""

import importlib.util
import os
import pathlib
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time

GO_ON = "Continue from where you left off."
HERE = pathlib.Path(__file__).resolve().parent
MARKERS = ("terminal", "container")
PODMAN = """#!/bin/bash
echo "$*" >>"$LOG"
case "$1 $*" in
"exec "*agent-run.sh*)
\twhile [ -d "$STATE" ]; do
\t\t[ -e "$STATE/quit" ] && { rm -f "$STATE/quit"; exit 0; }
\t\tsleep 0.1
\tdone ;;
"exec "*agent-takeover.py*) touch "$AGENT/handoff" ;;
"exec "*) cat >/dev/null ;;
"inspect "*) echo true ;;
"ps "*) [ -e "$STATE/window" ] && echo c1 ;;
esac
"""
# The transcript of a session cut at a tool call, and of one that ended its turn.
CUT = [
    '{"type": "user", "permissionMode": "acceptEdits", "message": {"content": "task"}}',
    '{"type": "assistant", "message": {"content": [{"type": "tool_use"}]}}',
]
ENDED = [*CUT, '{"type": "user", "message": {"content": [{"type": "tool_result"}]}}']
ENDED.append('{"type": "assistant", "message": {"content": [{"type": "text"}]}}')


def run_wrapper(root, bundle):
    """Run agent-terminal.py BUNDLE on the folders of `root`, its container always `c1`."""
    spec = importlib.util.spec_from_file_location("agent_terminal", HERE / "agent-terminal.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def up(folder):
        with open(os.environ["LOG"], "a") as log:
            log.write(f"up {folder}\n")
        return "c1"

    module.up = up
    module.copy_gitconfig = lambda container: None
    module.get_agent_folder = lambda name: root / name / ".agent"
    module.get_worktree_bundle_folder = lambda name: str(root / name)
    module.has_window = lambda container: bool(os.environ.get("WINDOW")) or (
        pathlib.Path(os.environ["STATE"]) / "window"
    ).exists()
    module.TRANSCRIPTS = root
    module.WINDOW_WAIT = float(os.environ.get("WINDOW_WAIT") or module.WINDOW_WAIT)
    sys.argv = ["agent-terminal.py", bundle]
    module.main()


def wait(condition, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


class Terminal:
    def __init__(self, root, env):
        self.pid, self.fd = pty.fork()
        if not self.pid:
            os.environ.update(env)
            os.execv(sys.executable, [sys.executable, __file__, "--run", str(root), "b1"])
        self.out = b""

    def read(self):
        try:
            while select.select([self.fd], [], [], 0)[0]:
                data = os.read(self.fd, 4096)
                if not data:
                    break
                self.out += data
        except OSError:
            pass
        return self.out.decode(errors="replace")

    def has_ended(self):
        self.read()
        try:
            return bool(os.waitpid(self.pid, os.WNOHANG)[0])
        except ChildProcessError:
            return True


class Case:
    def __init__(self, folder, name, window=False, tab=None, wait=None):
        self.name = name
        self.root = pathlib.Path(folder)
        self.agent = self.root / "b1" / ".agent"
        (self.agent / "run.lock").mkdir(parents=True)
        (self.agent / "task.md").write_text("task\n")
        (self.agent / "session").write_text("s1\n")
        self.state = self.root / "state"
        self.state.mkdir()
        if tab:
            (self.state / "window").touch()
            (self.agent / "tab-opened").touch()
            (self.root / "s1.jsonl").write_text("\n".join(tab) + "\n")
        self.terminator = subprocess.Popen(["sleep", "60"])
        (self.root / "bin").mkdir()
        (self.root / "bin" / "podman").write_text(PODMAN)
        (self.root / "bin" / "podman").chmod(0o755)
        self.env = {
            "AGENT": str(self.agent),
            "BUNDLE_CTL_WINDOW_PID": str(self.terminator.pid),
            "LOG": str(self.root / "log"),
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "STATE": str(self.state),
            "WINDOW": "1" if window else "",
            "WINDOW_WAIT": str(wait or ""),
        }
        self.failures = []
        self.count = 0
        self.terminals = []
        self.terminal = self.open()

    def open(self):
        terminal = Terminal(self.root, self.env)
        self.terminals.append(terminal)
        return terminal

    def check(self, what, passed):
        self.count += 1
        if not passed:
            self.failures.append(f"{self.name}: {what}")

    def log(self):
        path = self.root / "log"
        return path.read_text().splitlines() if path.exists() else []

    def runs(self):
        return [line for line in self.log() if line.startswith("exec") and "agent-run.sh" in line]

    def is_clean(self):
        return not any((self.agent / name).exists() for name in MARKERS)

    def start(self):
        self.check("claude runs", wait(lambda: self.runs() and (self.agent / "container").exists()))

    def check_stopped(self, what):
        self.check(f"{what} ends it", wait(self.terminal.has_ended))
        self.check(f"{what} stops the container", wait(lambda: self.log()[-1] == "stop c1"))


def check_quit(case):
    case.start()
    (case.state / "quit").touch()
    case.check_stopped("quitting claude")
    case.check("quitting claude keeps its window", case.terminator.poll() is None)


def check_handoff(case):
    case.start()
    (case.agent / "handoff").touch()
    (case.state / "quit").touch()
    case.check("a takeover ends it", wait(case.terminal.has_ended))
    closed = wait(lambda: case.terminator.poll() == -signal.SIGUSR1)
    case.check("a takeover closes its window", closed)
    case.check("a takeover keeps the container", "stop c1" not in case.log())


def check_sigterm(case):
    case.start()
    os.kill(case.terminal.pid, signal.SIGTERM)
    case.check_stopped("SIGTERM")


def check_close(case):
    case.start()
    os.close(case.terminal.fd)
    case.check_stopped("closing the terminal")


def check_close_window(case):
    case.start()
    os.close(case.terminal.fd)
    case.check("closing the terminal ends it", wait(case.terminal.has_ended))
    handed = wait(lambda: any("agent-takeover.py" in line for line in case.log()))
    case.check("closing the terminal hands the session to the window", handed)
    case.check("closing the terminal keeps the container", "stop c1" not in case.log())


def check_window_closes(case, go_on):
    asked = wait(lambda: (case.agent / "close-window").exists())
    case.check("it asks the window to close", asked)
    case.check("it keeps the terminal marker while it waits", (case.agent / "terminal").exists())
    case.check("it runs no claude while the window is open", not case.runs())
    (case.state / "window").unlink()
    case.start()
    case.check(f"it passes the go-on prompt: {go_on}", go_on == case.runs()[0].endswith(GO_ON))
    mode = case.agent / "mode"
    case.check("it keeps the mode of the tab", mode.exists() and mode.read_text() == "acceptEdits")
    for name in ("close-window", "tab-opened"):
        case.check(f"it removes {name}", not (case.agent / name).exists())
    (case.state / "quit").touch()
    case.check_stopped("quitting claude")


def check_from_window(case):
    check_window_closes(case, go_on=True)


def check_from_idle_window(case):
    check_window_closes(case, go_on=False)


def check_window_stays(case):
    stayed = wait(lambda: "window stayed open" in case.terminal.read())
    case.check("it gives up on the window", stayed)
    case.check("it runs no claude", not case.runs())
    case.check("it keeps the container", "stop c1" not in case.log())
    case.check("it removes close-window", not (case.agent / "close-window").exists())


def check_close_while_waiting(case):
    close = case.agent / "close-window"
    wait(close.exists)
    os.close(case.terminal.fd)
    case.check("closing the terminal ends it", wait(case.terminal.has_ended))
    takeover = any("agent-takeover.py" in line for line in case.log())
    case.check("closing the terminal leaves the claude of the tab", not takeover)
    case.check("closing the terminal keeps the container", "stop c1" not in case.log())
    case.check("closing the terminal removes close-window", not close.exists())


def main():
    failures, total = [], 0
    tabs = {
        check_from_window: CUT,
        check_from_idle_window: ENDED,
        check_window_stays: CUT,
        check_close_while_waiting: CUT,
    }
    checks = (check_quit, check_sigterm, check_close, check_close_window, check_handoff, *tabs)
    for check in checks:
        with tempfile.TemporaryDirectory() as folder:
            wait_time = 1 if check is check_window_stays else None
            window = check is check_close_window
            case = Case(folder, check.__name__[6:], window, tabs.get(check), wait_time)
            try:
                check(case)
                case.check("its markers are gone", wait(case.is_clean))
            finally:
                for terminal in case.terminals:
                    if not terminal.has_ended():
                        os.kill(terminal.pid, signal.SIGKILL)
                case.terminator.kill()
        failures += case.failures
        total += case.count
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--run"]:
        run_wrapper(pathlib.Path(sys.argv[2]), sys.argv[3])
    else:
        main()
