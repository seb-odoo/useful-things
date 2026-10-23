"""Open VS Code attached to a bundle's dev container, or with -t the terminator window of its agent.
A bundle that is not local is fetched first, with pfb.

Example:
 $ python ~/repo/useful-things/scripts/open_bundle.py master-bundle-name--seb
 $ python ~/repo/useful-things/scripts/open_bundle.py -t master-bundle-name--seb
"""

import argparse
import os
import subprocess
import sys

from branch_status import get_bundles
from commands import clean_bundle_name, get_worktree_bundle_folder
from rich import print
from utils import UtilsRunner

runner = UtilsRunner()

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the bundle to open in VS Code", type=str)
parser.add_argument(
    "-t",
    "--terminal",
    action="store_true",
    help="the terminator window of its agent, a new one when it has none, rather than VS Code",
)
args = parser.parse_args()
bundle_name = clean_bundle_name(args.name)

if not os.path.isdir(get_worktree_bundle_folder(bundle_name)):
    print(f"Bundle [yellow]{bundle_name}[/yellow] not found locally, fetching it")
    fetch_bundle = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_bundle.py")
    if subprocess.run([sys.executable, fetch_bundle, args.name, "--no-open"], check=False).returncode:
        sys.exit(1)

if args.terminal:
    if not runner.open_agent_terminal(bundle_name=bundle_name):
        sys.exit(1)
else:
    runner.open_devcontainer(bundle_name=bundle_name)
get_bundles()
