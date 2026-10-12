"""Open a bundle from an `odoo-bundle://<bundle name>` link, the one gbs puts on each branch.

A bundle with a worktree opens in its dev container, like ocode. Any other one goes through pfb in
a terminal, which opens the dev container once the worktrees are built. `odoo-bundle://ops`, which
hok puts on a request of the ops container, opens that one, like orepo.

Examples:
 $ python ~/repo/useful-things/scripts/bundle_open.py odoo-bundle://master-bundle-name--seb
"""

import argparse
import os
import shlex
import subprocess
import sys

from commands import clean_bundle_name, get_worktree_bundle_folder
from config import HOST_FOLDER
from utils import UtilsRunner

SCHEME = "odoo-bundle://"

parser = argparse.ArgumentParser()
parser.add_argument("link", help="odoo-bundle:// link, or bare bundle name", type=str)
args = parser.parse_args()
bundle_name = clean_bundle_name(args.link.removeprefix(SCHEME).strip("/"))

if bundle_name == "ops" and HOST_FOLDER:
    UtilsRunner().open_devcontainer_folder(folder=HOST_FOLDER)
elif os.path.isdir(get_worktree_bundle_folder(bundle_name)):
    UtilsRunner().open_devcontainer(bundle_name=bundle_name)
else:
    fetch_bundle = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_bundle.py")
    pfb = shlex.join([sys.executable, fetch_bundle, bundle_name])
    subprocess.Popen(
        ["gnome-terminal", "--", "bash", "-c", f"{pfb} || read -rp 'pfb failed, press Enter'"],
    )
