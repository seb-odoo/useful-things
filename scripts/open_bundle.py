"""Open VS Code attached to an existing bundle's dev container.

Example:
 $ python ~/repo/useful-things/scripts/open_bundle.py master-bundle-name--seb
"""

from rich import print

import argparse
import os

from commands import clean_bundle_name, get_worktree_bundle_folder
from utils import UtilsRunner

runner = UtilsRunner()

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name of the bundle to open in VS Code", type=str)
args = parser.parse_args()
bundle_name = clean_bundle_name(args.name)

if not os.path.isdir(get_worktree_bundle_folder(bundle_name)):
    print(f"Bundle [red]{bundle_name}[/red] not found locally; run gnb or pfb first.")
    exit(1)

runner.open_devcontainer(bundle_name=bundle_name)
