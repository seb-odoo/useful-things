"""Open VS Code attached to an existing bundle's dev container, or with -t the terminator window of
its agent.

Example:
 $ python ~/repo/useful-things/scripts/open_bundle.py master-bundle-name--seb
 $ python ~/repo/useful-things/scripts/open_bundle.py -t master-bundle-name--seb
"""

import argparse
import os
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
    print(f"Bundle [red]{bundle_name}[/red] not found locally; run gnb or pfb first.")
    exit(1)

if args.terminal:
    if not runner.open_agent_terminal(bundle_name=bundle_name):
        sys.exit(1)
else:
    runner.open_devcontainer(bundle_name=bundle_name)
get_bundles()
