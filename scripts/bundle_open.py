"""Open a bundle from an `odoo-bundle://<bundle name>` link.

A bundle with a worktree opens in its dev container, like ocode. Any other one goes through pfb in
a terminal, which opens the dev container once the worktrees are built.
`odoo-bundle://terminal/NAME`, the one gbs puts on each branch, brings up the VS Code window of the
bundle when one is open, else the terminal of its agent, like ocode -t, and marks it handled; a
bundle that is not local goes through pfb -t in a terminal. `odoo-bundle://ops`, which
hok puts on a request of the ops container with no session, opens that one, like orepo.
`odoo-bundle://ops-SESSION`, which the shell of an ops agent window prints, also moves its session
to a tab there. `odoo-bundle://agent/NAME`, which hok puts on the caller of a request, brings up
the agent of a bundle or of an ops session (ops-SESSION), as the agent list does.

The band of an agent terminal has three more: `odoo-bundle://hok/NAME` brings up the agent list on
the request NAME of hok, `odoo-bundle://list/NAME` on the row of that agent, and
`odoo-bundle://tabs/BUNDLE` brings up the Firefox tab group of the bundle with the pages it lacks:
the thread and the diff of each PR runbot knows for it, and its page on runbot.

Examples:
 $ python ~/repo/useful-things/scripts/bundle_open.py odoo-bundle://master-bundle-name--seb
"""

import argparse
import os
import re
import shlex
import subprocess
import sys
import urllib.parse

import requests
from agents import OPS, mark_handled
from branch_status import get_bundles
from commands import clean_bundle_name, get_worktree_bundle_folder
from config import HOST_FOLDER, REPO_ROOT
from utils import UtilsRunner

AGENT = "agent/"
BCTL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bundle-ctl/client/bctl.py")
AGENT_LIST = os.path.join(os.path.dirname(BCTL), "..", "agent-list.py")
HOK = "hok/"
HOK_NAME = re.compile(r"[A-Za-z0-9+._ -]{1,120}")
LIST = "list/"
OPEN_GROUP = os.path.join(REPO_ROOT, "firefox-gateway", "open_group.py")
PICKER = os.path.join(os.path.dirname(BCTL), "..", "window-picker.py")
RUNBOT = "https://runbot.odoo.com"
SCHEME = "odoo-bundle://"
TABS = "tabs/"
TERMINAL = "terminal/"
TITLE = os.path.join(os.path.dirname(BCTL), "window-title.py")

parser = argparse.ArgumentParser()
parser.add_argument("link", help="odoo-bundle:// link, or bare bundle name", type=str)
args = parser.parse_args()
link = urllib.parse.unquote(args.link.removeprefix(SCHEME).strip("/"))
bundle_name = clean_bundle_name(link.partition("/")[2] or link)


def notify(error):
    subprocess.run(["notify-send", "--app-name=bundle-ctl", "--", bundle_name, error], check=False)


def run_or_notify(command):
    res = subprocess.run(command, capture_output=True, check=False, text=True)
    if res.returncode:
        notify((res.stderr or res.stdout).strip())
    return res.returncode


def start(command):
    """A window that stays, which this handler does not wait for."""
    null = subprocess.DEVNULL
    subprocess.Popen(command, start_new_session=True, stdin=null, stdout=null, stderr=null)


def get_pages(bundle):
    """The thread and the diff of each PR runbot knows for the bundle, then its page on runbot."""
    try:
        found = requests.get(f"{RUNBOT}/api/bundle", params={"name": bundle}, timeout=10).json()
    except (requests.RequestException, ValueError):
        return []
    pages = []
    for branch in found.get("branches") or []:
        repo = re.search(r"github\.com[:/](.+?)(?:\.git)?$", branch.get("remote") or "")
        if branch.get("is_pr") and repo:
            thread = f"https://github.com/{repo[1]}/pull/{branch['name']}"
            # GitHub redirects /files to /changes, and the extension finds an open page by its URL.
            pages += [thread, f"{thread}/changes"]
    return pages + [f"{RUNBOT}/runbot/bundle/{found['id']}"] * bool(found.get("id"))


def open_tabs(bundle):
    """The tab group of the bundle, named as firefox-tabs names it: the icon and the name of its
    window title."""
    pages = get_pages(bundle)
    if not pages:
        notify("runbot knows nothing of this bundle: no page to open")
        return
    title = subprocess.run(
        [sys.executable, TITLE, bundle], capture_output=True, check=False, text=True
    ).stdout.strip()
    run_or_notify([sys.executable, "-I", OPEN_GROUP, title.rpartition(" (")[0], *pages])


if link.startswith(HOK) and not HOK_NAME.fullmatch(link.removeprefix(HOK)):
    notify("not the name of a request of hok")
elif link.startswith(HOK):
    start([sys.executable, AGENT_LIST, "--select", f"hok:{link.removeprefix(HOK)}"])
elif link.startswith(LIST):
    start([sys.executable, AGENT_LIST, "--select", bundle_name])
elif link.startswith(TABS):
    open_tabs(bundle_name)
elif link.startswith(AGENT):
    run_or_notify([sys.executable, PICKER, "--agent", bundle_name])
elif link.startswith(TERMINAL) and os.path.isdir(get_worktree_bundle_folder(bundle_name)):
    if not run_or_notify([sys.executable, PICKER, "--agent", bundle_name]):
        mark_handled(bundle_name)
        get_bundles()
elif bundle_name == "ops" and HOST_FOLDER:
    UtilsRunner().open_devcontainer_folder(folder=HOST_FOLDER)
elif bundle_name.startswith(OPS) and HOST_FOLDER:
    run_or_notify([sys.executable, BCTL, "open", bundle_name])
elif os.path.isdir(get_worktree_bundle_folder(bundle_name)):
    UtilsRunner().open_devcontainer(bundle_name=bundle_name)
    get_bundles()
else:
    fetch_bundle = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_bundle.py")
    pfb = shlex.join(
        [sys.executable, fetch_bundle, bundle_name, *["-t"] * link.startswith(TERMINAL)]
    )
    subprocess.Popen(
        ["gnome-terminal", "--", "bash", "-c", f"{pfb} || read -rp 'pfb failed, press Enter'"],
    )
