"""Open a bundle from an `odoo-bundle://<bundle name>` link, the one gbs puts on each branch.

A bundle with a worktree opens in its dev container, like ocode. Any other one goes through pfb in
a terminal, which opens the dev container once the worktrees are built. `odoo-bundle://ops`, which
hok puts on a request of the ops container with no session, opens that one, like orepo.
`odoo-bundle://ops-SESSION`, which the shell of an ops agent window prints, also moves its session
to a tab there. `odoo-bundle://agent/NAME`, which hok puts on the caller of a request, brings up
the agent of a bundle or of an ops session (ops-SESSION), as the agent list does.

Examples:
 $ python ~/repo/useful-things/scripts/bundle_open.py odoo-bundle://master-bundle-name--seb
"""

import argparse
import os
import shlex
import subprocess
import sys

from agents import OPS
from branch_status import get_bundles
from commands import clean_bundle_name, get_worktree_bundle_folder
from config import HOST_FOLDER
from utils import UtilsRunner

AGENT = "agent/"
BCTL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bundle-ctl/client/bctl.py")
PICKER = os.path.join(os.path.dirname(BCTL), "..", "window-picker.py")
SCHEME = "odoo-bundle://"

parser = argparse.ArgumentParser()
parser.add_argument("link", help="odoo-bundle:// link, or bare bundle name", type=str)
args = parser.parse_args()
link = args.link.removeprefix(SCHEME).strip("/")
bundle_name = clean_bundle_name(link.removeprefix(AGENT))


def run_or_notify(command):
    res = subprocess.run(command, capture_output=True, check=False, text=True)
    if res.returncode:
        error = (res.stderr or res.stdout).strip()
        notify = ["notify-send", "--app-name=bundle-ctl", "--", bundle_name, error]
        subprocess.run(notify, check=False)


if link.startswith(AGENT):
    run_or_notify([sys.executable, PICKER, "--agent", bundle_name])
elif bundle_name == "ops" and HOST_FOLDER:
    UtilsRunner().open_devcontainer_folder(folder=HOST_FOLDER)
elif bundle_name.startswith(OPS) and HOST_FOLDER:
    run_or_notify([sys.executable, BCTL, "open", bundle_name])
elif os.path.isdir(get_worktree_bundle_folder(bundle_name)):
    UtilsRunner().open_devcontainer(bundle_name=bundle_name)
    get_bundles()
else:
    fetch_bundle = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_bundle.py")
    pfb = shlex.join([sys.executable, fetch_bundle, bundle_name])
    subprocess.Popen(
        ["gnome-terminal", "--", "bash", "-c", f"{pfb} || read -rp 'pfb failed, press Enter'"],
    )
