"""Open VS Code attached to the dev container of any folder, bundle or not.

Example:
 $ python ~/repo/useful-things/scripts/open_folder.py ~/repo/mail-agent
"""

from rich import print

import argparse
import os

from utils import UtilsRunner

runner = UtilsRunner()

parser = argparse.ArgumentParser()
parser.add_argument("folder", help="Folder holding the .devcontainer to open", type=str)
args = parser.parse_args()
folder = os.path.realpath(os.path.expanduser(args.folder))

if not os.path.isfile(f"{folder}/.devcontainer/devcontainer.json"):
    print(f"No [red]{folder}/.devcontainer/devcontainer.json[/red] to open.")
    exit(1)

runner.open_devcontainer_folder(folder=folder)
