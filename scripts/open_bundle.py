"""Open VS Code attached to an existing bundle's dev container, or with -t a shell of that container
in this terminal.

Example:
 $ python ~/repo/useful-things/scripts/open_bundle.py master-bundle-name--seb
 $ python ~/repo/useful-things/scripts/open_bundle.py -t master-bundle-name--seb
"""

import argparse
import importlib.util
import os
import pathlib
import signal
import subprocess
import sys
import threading

from rich import print

from agents import get_container, has_window, is_agent_running, mark_handled
from branch_status import get_bundles
from commands import clean_bundle_name, get_worktree_bundle_folder
from utils import UtilsRunner

TERMINAL = pathlib.Path(__file__).resolve().parent.parent / "bundle-ctl" / "agent-terminal.py"


def load_terminal():
    spec = importlib.util.spec_from_file_location("agent_terminal", TERMINAL)
    terminal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(terminal)
    return terminal


def open_shell(bundle_name):
    """A bash of the bundle's dev container in this terminal. A container it started stops with the
    shell, unless a VS Code window or an agent uses it by then."""
    terminal = load_terminal()
    folder = get_worktree_bundle_folder(bundle_name)
    container = get_container(folder)
    started = not container
    if started:
        try:
            container = terminal.up(folder)
        except terminal.Failure as error:
            print(f"[red]{bundle_name}[/red]: {error}")
            sys.exit(1)
        terminal.copy_gitconfig(container)
        pg = terminal.HERE.parent / "container-rw" / "bundle-pg.sh"
        subprocess.run(["podman", "exec", "--user", terminal.USER, container, "bash", pg], check=False)
    signal.signal(signal.SIGHUP, lambda *args: sys.exit(1))
    try:
        subprocess.run(
            ["podman", "exec", "--interactive", "--tty", "--detach-keys=", "--user", terminal.USER]
            + ["--workdir", "/workspace", "--env", f"TERM={os.environ.get('TERM', 'xterm')}"]
            + [container, "bash"],
            check=False,
        )
    finally:
        if started and not has_window(container) and not is_agent_running(bundle_name):
            subprocess.run(["podman", "stop", container], check=False, capture_output=True)


runner = UtilsRunner()

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the bundle to open in VS Code", type=str)
parser.add_argument(
    "-t",
    "--terminal",
    action="store_true",
    help="a shell of its dev container in this terminal, rather than VS Code",
)
args = parser.parse_args()
bundle_name = clean_bundle_name(args.name)

if not os.path.isdir(get_worktree_bundle_folder(bundle_name)):
    print(f"Bundle [red]{bundle_name}[/red] not found locally; run gnb or pfb first.")
    exit(1)

if args.terminal:
    mark_handled(bundle_name)
    threading.Thread(target=get_bundles).start()
    open_shell(bundle_name)
else:
    runner.open_devcontainer(bundle_name=bundle_name)
    get_bundles()
