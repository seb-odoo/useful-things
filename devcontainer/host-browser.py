#!/usr/bin/python3 -I
"""The browser VS Code opens a link with: "workbench.externalBrowser" names this file.

    host-browser.py URL...                      hands the pages to firefox-gateway, or refuses them
    HOST_BROWSER_DRY=1 host-browser.py URL...   prints "open" or "refused" for each, opens nothing

VS Code runs it for a link of any of its windows: `$BROWSER` and `code --openExternal` in a dev
container, a click on a link. The URL of a page a container asks for can carry data out of the
network of the container, and a page the container serves runs its scripts in the browser of the
host. So nothing opens here: each page goes to firefox-gateway, cloned beside this repo, which
checks it against the hosts of its config (a local page on plain http port 80 only, where the
Odoo proxy of the bundles listens) and holds it for a yes in hok, as the pages a container asks
for. VS Code gives the URL alone, so the card cannot say which window asked, and the page opens
in no tab group. A page refused, or with no firefox-gateway, is a notification that says why.
"""

import os
import pathlib
import subprocess
import sys

GATEWAY = pathlib.Path(__file__).resolve().parents[2] / "firefox-gateway" / "host_open.py"
TIMEOUT = 90


def ask(*args):
    """The exit code of firefox-gateway, and what it said."""
    if not GATEWAY.is_file():
        return 1, f"no {GATEWAY.parent}: no page a VS Code window asks for opens"
    try:
        ran = subprocess.run(
            [sys.executable, "-I", str(GATEWAY), *args], capture_output=True, check=False, text=True, timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return 1, str(error)
    return ran.returncode, (ran.stdout if ran.returncode == 0 else ran.stderr or ran.stdout).strip()


def main():
    urls = sys.argv[1:]
    if os.environ.get("HOST_BROWSER_DRY"):
        code, said = ask("--check", *urls)
        lines = said.splitlines() if code == 0 else [f"refused: {said}"] * len(urls)
        for line in lines:
            print(line.split(":")[0])
            if line != "open":
                print(line, file=sys.stderr)
        return
    code, said = ask(*urls)
    if code != 0:
        text = f"{said}\n{' '.join(urls)}"[:500].replace("&", "&amp;").replace("<", "&lt;")
        subprocess.run(["/usr/bin/notify-send", "--", "A page was not opened", text], check=False)


if __name__ == "__main__":
    main()
