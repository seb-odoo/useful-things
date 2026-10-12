#!/usr/bin/env python3
"""What agent-terminal.py does when its container is stopped, with a fake podman and the wrapper in a
pty: python3 test_terminal.py
"""

import importlib.util
import os
import pathlib
import pty
import select
import signal
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
GO_ON = "Continue from where"
MARKERS = ("terminal", "container", "park", "parked")
PODMAN = """#!/bin/bash
echo "$*" >>"$LOG"
case "$1 $*" in
"exec "*agent-run.sh*)
\twhile [ -d "$STATE" ]; do
\t\t[ -e "$STATE/stopped" ] && { rm -f "$STATE/stopped"; exit 137; }
\t\t[ -e "$STATE/quit" ] && { rm -f "$STATE/quit"; exit 0; }
\t\tsleep 0.1
\tdone ;;
"exec "*) cat >/dev/null ;;
"stop "*) touch "$STATE/stopped" ;;
"inspect "*) echo true ;;
esac
"""


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
    module.has_window = lambda container: False
    module.PARK_CHECK = 0.2
    module.TRANSCRIPTS = root
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
    def __init__(self, folder, name):
        self.name = name
        self.root = pathlib.Path(folder)
        self.agent = self.root / "b1" / ".agent"
        (self.agent / "run.lock").mkdir(parents=True)
        (self.agent / "task.md").write_text("task\n")
        (self.agent / "session").write_text("s1\n")
        self.state = self.root / "state"
        self.state.mkdir()
        (self.root / "bin").mkdir()
        (self.root / "bin" / "podman").write_text(PODMAN)
        (self.root / "bin" / "podman").chmod(0o755)
        self.env = {
            "LOG": str(self.root / "log"),
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "STATE": str(self.state),
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

    def park(self):
        self.check("claude runs", wait(lambda: self.runs() and (self.agent / "container").exists()))
        (self.agent / "park").touch()
        self.check(
            "park stops the container",
            wait(lambda: (self.agent / "parked").exists() and "stop c1" in self.log()),
        )
        self.check("parked drops the container", wait(lambda: not (self.agent / "container").exists()))
        self.check("parked asks for Enter", wait(lambda: "Press Enter" in self.terminal.read()))


def check_enter(case):
    case.park()
    case.check("parked keeps the terminal", not case.terminal.has_ended())
    os.write(case.terminal.fd, b"\n")
    case.check(
        "Enter starts the container again",
        wait(lambda: len(case.runs()) == 2 and (case.agent / "container").exists()),
    )
    case.check("Enter calls up again", sum(line.startswith("up ") for line in case.log()) == 2)
    case.check("Enter resumes with no prompt", GO_ON not in case.runs()[-1])
    (case.state / "quit").touch()
    case.check("quitting claude ends it", wait(case.terminal.has_ended))
    case.check("quitting claude stops the container", case.log()[-1] == "stop c1")


def check_sigterm(case):
    case.park()
    os.kill(case.terminal.pid, signal.SIGTERM)
    case.check("SIGTERM ends it", wait(case.terminal.has_ended))


def check_close(case):
    case.park()
    os.close(case.terminal.fd)
    case.check("closing the terminal ends it", wait(case.terminal.has_ended))


def check_take_over(case):
    case.park()
    other = case.open()
    case.check("another terminal ends the parked one", wait(case.terminal.has_ended))
    case.check(
        "the other terminal starts the container",
        wait(lambda: len(case.runs()) == 2 and read_pid(case.agent) == other.pid),
    )
    case.check("the other terminal resumes with no prompt", GO_ON not in case.runs()[-1])
    (case.state / "quit").touch()
    case.check("quitting claude ends the other terminal", wait(other.has_ended))


def read_pid(agent):
    try:
        return int((agent / "terminal").read_text())
    except (OSError, ValueError):
        return None


def main():
    failures, total = [], 0
    for check in (check_enter, check_sigterm, check_close, check_take_over):
        with tempfile.TemporaryDirectory() as folder:
            case = Case(folder, check.__name__[6:])
            try:
                check(case)
                case.check("its markers are gone", wait(case.is_clean))
            finally:
                for terminal in case.terminals:
                    if not terminal.has_ended():
                        os.kill(terminal.pid, signal.SIGKILL)
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
