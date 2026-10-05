#!/usr/bin/env python3
"""Ask the host bundle-ctl daemon for bundle work, from a dev container or from the host.

    bctl whoami
    bctl status [--json]
    bctl create BASE NAME [--branch-repo REPO]... [--no-open]
    bctl fetch BUNDLE [--no-open]
    bctl open BUNDLE

create, fetch and open run as a job on the host: bctl prints its log until it ends, which can take
minutes for a new bundle.
"""

import argparse
import http.client
import json
import os
import pathlib
import socket
import sys

SOCKETS = (
    pathlib.Path("/run/bundle-ctl/ctl.sock"),
    pathlib.Path(os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local/state")
    / "bundle-ctl/sock/ctl.sock",
)


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path, timeout):
        super().__init__("bundle-ctl", timeout=timeout)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.socket_path))


def request(method, path, body=None, timeout=120):
    socket_path = next((path for path in SOCKETS if path.exists()), None)
    if socket_path is None:
        sys.exit("bctl: no bundle-ctl socket, is the daemon running on the host?")
    connection = UnixConnection(socket_path, timeout)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    try:
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        answer = json.loads(response.read() or b"{}")
    except OSError as error:
        sys.exit(f"bctl: {socket_path}: {error}")
    if response.status >= 300:
        sys.exit(f"bctl: {response.status} {answer.get('error', '')}")
    return answer


def format_repo(repo, status):
    if not status:
        return f"{repo} ?"
    return f"{repo} -{status['behind']}{' conflict' if status['conflict'] else ''}"


def print_status(answer):
    for row in answer["bundles"]:
        repos = ", ".join(format_repo(repo, status) for repo, status in row["repos"].items())
        state = "open" if row["open"] else "folder" if row["folder"] else "branch"
        print(f"{row['bundle']:60} {state:6} {repos}")


def follow(job_id):
    offset = 0
    while True:
        answer = request("GET", f"/job?id={job_id}&offset={offset}&wait=50", timeout=70)
        sys.stdout.write(answer["log"])
        sys.stdout.flush()
        offset = answer["offset"]
        if answer["state"] == "failed":
            sys.exit(f"bctl: {answer['result']['error']}")
        if answer["state"] == "done":
            return answer["result"]


def main():
    parser = argparse.ArgumentParser(prog="bctl")
    parser.add_argument("--json", action="store_true", help="print the raw answer")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("whoami", help="the bundle of this container, its base and parent")
    commands.add_parser("status", help="every local bundle: behind/conflict per repo, open")
    create = commands.add_parser("create", help="a new bundle BASE-NAME--seb, branched on BASE")
    create.add_argument("base")
    create.add_argument("name")
    create.add_argument(
        "--branch-repo",
        action="append",
        dest="branch_repos",
        help="repo that gets the branch, repeatable (default: odoo)",
    )
    create.add_argument("--no-open", action="store_true")
    fetch = commands.add_parser("fetch", help="an existing bundle from runbot or the dev remotes")
    fetch.add_argument("name")
    fetch.add_argument("--no-open", action="store_true")
    open_ = commands.add_parser("open", help="a VS Code window on a local bundle")
    open_.add_argument("bundle")
    args = parser.parse_args()

    printer = None
    if args.command == "whoami":
        answer = request("GET", "/whoami")
    elif args.command == "status":
        answer = request("GET", "/status")
        printer = print_status
    elif args.command == "create":
        body = {"base": args.base, "name": args.name, "open": not args.no_open}
        if args.branch_repos:
            body["branch_repos"] = args.branch_repos
        answer = follow(request("POST", "/create", body)["job"])
    elif args.command == "fetch":
        body = {"name": args.name, "open": not args.no_open}
        answer = follow(request("POST", "/fetch", body)["job"])
    else:
        answer = follow(request("POST", "/open", {"bundle": args.bundle})["job"])
    if args.json or printer is None:
        print(json.dumps(answer, indent=2))
    else:
        printer(answer)


if __name__ == "__main__":
    main()
