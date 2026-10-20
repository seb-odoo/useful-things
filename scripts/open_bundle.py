"""Open VS Code attached to an existing bundle's dev container, or with -t the terminator window of
its agent.

Example:
 $ python ~/repo/useful-things/scripts/open_bundle.py master-bundle-name--seb
 $ python ~/repo/useful-things/scripts/open_bundle.py -t master-bundle-name--seb
"""

import argparse
import os
import subprocess
import sys

from agents import mark_handled
from branch_status import get_bundles
from commands import clean_bundle_name, get_worktree_bundle_folder
from rich import print
from utils import UtilsRunner

BCTL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bundle-ctl/client/bctl.py")

runner = UtilsRunner()

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the bundle to open in VS Code", type=str)
parser.add_argument(
    "-t",
    "--terminal",
    action="store_true",
    help="the terminator window of its agent, on its last session, rather than VS Code",
)
args = parser.parse_args()
bundle_name = clean_bundle_name(args.name)

if not os.path.isdir(get_worktree_bundle_folder(bundle_name)):
    print(f"Bundle [red]{bundle_name}[/red] not found locally; run gnb or pfb first.")
    exit(1)

if args.terminal:
    command = [sys.executable, BCTL, "open", bundle_name, "--terminal"]
    if subprocess.run(command, check=False).returncode:
        sys.exit(1)
    mark_handled(bundle_name)
else:
    runner.open_devcontainer(bundle_name=bundle_name)
get_bundles()
