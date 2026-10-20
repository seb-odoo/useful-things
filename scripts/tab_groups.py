"""The Firefox tab groups of the bundles, from the helper of firefox-gateway on its host socket:
one JSON line asked, one JSON line answered. No socket or no answer: Firefox or its helper does
not run, and no bundle has a group.
"""

import json
import os
import pathlib
import socket

RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
SOCKET = pathlib.Path(RUNTIME) / "firefox-gateway" / "host.sock"
TIMEOUT = 5


def ask(request):
    """The answer of the helper to one request, None when it does not answer."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(TIMEOUT)
            connection.connect(str(SOCKET))
            connection.sendall(json.dumps(request).encode() + b"\n")
            with connection.makefile("rb") as answer:
                return json.loads(answer.readline())
    except (OSError, ValueError):
        return None


def get_groups():
    """The groups of each bundle, by bundle."""
    groups = {}
    for group in (ask({"op": "groups"}) or {}).get("groups") or []:
        if group.get("bundle"):
            groups.setdefault(group["bundle"], []).append(group)
    return groups
