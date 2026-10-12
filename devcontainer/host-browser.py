#!/usr/bin/python3 -I
"""The browser VS Code opens a link with: "workbench.externalBrowser" names this file.

    host-browser.py URL...                      opens each URL, or refuses it
    HOST_BROWSER_DRY=1 host-browser.py URL...   prints "open" or "refused" for each, opens nothing

A page a dev container serves reaches the host browser as a localhost address, its port forwarded
into the container by VS Code with no prompt: its scripts would then run in the browser of the
host, outside the network of the container. Such an address is refused: a host ending in
localhost or a loopback address, on any port but plain http on 80, where the Odoo proxy of the
bundles listens. Anything else goes to the default browser.
"""

import ipaddress
import os
import subprocess
import sys
import urllib.parse


def is_local(host):
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def is_refused(url):
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return True
    if parts.scheme not in ("http", "https") or not is_local(parts.hostname or ""):
        return False
    return parts.scheme != "http" or port not in (None, 80)


def main():
    dry = os.environ.get("HOST_BROWSER_DRY")
    for url in sys.argv[1:]:
        refused = is_refused(url)
        if dry:
            print("refused" if refused else "open")
        elif refused:
            title = "A page a container serves was not opened"
            subprocess.run(["/usr/bin/notify-send", "--", title, url[:300]], check=False)
        else:
            subprocess.run(["/usr/bin/xdg-open", url], check=False)


if __name__ == "__main__":
    main()
