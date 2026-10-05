#!/usr/bin/env python3
"""Answer the bundle requests of the dev containers, on a unix socket every bundle container mounts.

    systemctl --user start bundle-ctl
    curl --unix-socket ~/.local/state/bundle-ctl/sock/ctl.sock http://bundle-ctl/whoami

A container reaches neither the host network nor podman, so this is its one way to the host, with
fixed verbs. The caller is the bundle whose container runs the connecting process, read from the
kernel and never from the request.
"""

from concurrent.futures import ThreadPoolExecutor
import http.server
import importlib.util
import json
import os
import pathlib
import re
import socket
import socketserver
import struct
import subprocess
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from branch_status import (  # noqa: E402
    get_local_branches,
    get_open_bundle_folders,
    get_status,
    has_write_tree,
)
from commands import (  # noqa: E402
    get_base_from_bundle_name,
    get_repo_folder,
    get_repos,
    get_sticky_bundles,
    get_worktree_bundle_folder,
)


def load_config():
    """devcontainer/config.py, under another name than the scripts' own config module."""
    path = HERE.parent / "devcontainer" / "config.py"
    spec = importlib.util.spec_from_file_location("odoo_dev_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load()


CONFIG = load_config()
CONTAINER_ID = re.compile(r"/libpod-(?:payload-)?([0-9a-f]{64})")
HOST = "host"
MAX_BODY = 64 * 1024
SOCKET = pathlib.Path(CONFIG["STATE_ROOT"]) / "bundle-ctl" / "sock" / "ctl.sock"

folder_by_container = {}


def get_container_folder(container_id):
    if container_id not in folder_by_container:
        res = subprocess.run(
            [
                "podman",
                "inspect",
                "--format",
                '{{index .Config.Labels "devcontainer.local_folder"}}',
                container_id,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        if res.returncode:
            return ""
        folder_by_container[container_id] = res.stdout.strip()
    return folder_by_container[container_id]


def identify(connection):
    """The bundle of the container running the peer process, HOST, or None when refused."""
    pid, _uid, _gid = struct.unpack(
        "3i",
        connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")),
    )
    try:
        cgroup = pathlib.Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return None
    if "libpod" not in cgroup:
        return HOST
    match = CONTAINER_ID.search(cgroup)
    folder = match and get_container_folder(match[1])
    bundle = os.path.basename(folder or "")
    if not bundle or folder != get_worktree_bundle_folder(bundle):
        return None
    return bundle


def whoami(caller, query, body):
    if caller == HOST:
        return 200, {"caller": HOST}
    parent = pathlib.Path(get_worktree_bundle_folder(caller)) / ".agent" / "parent"
    return 200, {
        "base": get_base_from_bundle_name(caller),
        "bundle": caller,
        "caller": caller,
        "parent": parent.read_text().strip() if parent.is_file() else None,
    }


def status(caller, query, body):
    repos = [repo for repo in get_repos() if os.path.isdir(get_repo_folder(repo))]
    write_tree = has_write_tree()
    repos_by_bundle = {}
    with ThreadPoolExecutor() as executor:
        for repo, branches in zip(repos, executor.map(get_local_branches, repos)):
            for branch, _date, _in_worktree in branches:
                if branch not in get_sticky_bundles(repo):
                    repos_by_bundle.setdefault(branch, []).append(repo)
        pairs = [(repo, bundle) for bundle, names in repos_by_bundle.items() for repo in names]
        open_future = executor.submit(get_open_bundle_folders)
        statuses = dict(
            zip(pairs, executor.map(lambda pair: get_status(*pair, write_tree), pairs)),
        )
        open_folders = open_future.result()
    rows = []
    for bundle, names in sorted(repos_by_bundle.items()):
        folder = get_worktree_bundle_folder(bundle)
        rows.append(
            {
                "base": get_base_from_bundle_name(bundle),
                "bundle": bundle,
                "folder": os.path.isdir(folder),
                "open": folder in open_folders,
                "repos": {repo: statuses[repo, bundle] for repo in names},
            },
        )
    return 200, {"bundles": rows}


VERBS = {
    ("GET", "/status"): status,
    ("GET", "/whoami"): whoami,
}


class Handler(http.server.BaseHTTPRequestHandler):
    caller = None

    def address_string(self):
        return self.caller or "refused"

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method):
        self.caller = identify(self.request)
        url = urllib.parse.urlsplit(self.path)
        verb = VERBS.get((method, url.path))
        length = int(self.headers.get("Content-Length") or 0)
        if self.caller is None:
            code, answer = 403, {"error": "not a bundle container"}
        elif verb is None:
            code, answer = 404, {"error": f"no verb {method} {url.path}"}
        elif length > MAX_BODY:
            code, answer = 413, {"error": f"body over {MAX_BODY} bytes"}
        else:
            try:
                body = json.loads(self.rfile.read(length)) if length else {}
                code, answer = verb(self.caller, urllib.parse.parse_qs(url.query), body)
            except ValueError as error:
                code, answer = 400, {"error": str(error)}
        print(
            f"{self.address_string()} {method} {url.path} {code} {answer.get('error', '')}".strip(),
            flush=True,
        )
        data = json.dumps(answer).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    os.umask(0o077)
    server = Server(str(SOCKET), Handler)
    print(f"listening on {SOCKET}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
