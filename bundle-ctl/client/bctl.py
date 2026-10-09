#!/usr/bin/env python3
"""Ask the host bundle-ctl daemon for bundle work, from a dev container or from the host.

    bctl whoami
    bctl status [--json]
    bctl branches [--json]
    bctl create BASE NAME [--branch-repo REPO]... [--no-open] [TASK]
    bctl fetch BUNDLE|PR_LINK|OWNER:BRANCH [--no-open] [TASK]
    bctl open BUNDLE [TASK] [--resume]

TASK is --task TEXT or --task-file FILE, with an optional --priority N.

create, fetch and open run as a job on the host: bctl prints its log until it ends, which can take
minutes for a new bundle. With a task, no window opens: once the bundle fits under the cap of
running bundles, Claude runs the task in a terminal of the host, in the bundle's container, and a
VS Code window opened on the bundle later takes the session over in a Claude tab. A bundle whose
window is already open runs the task in that window. The queue runs the highest priority first
(-100 to 100, default 0), then in arrival order: bctl status lists it in that order.

open --resume gives the task to the last Claude session of the bundle as its next prompt, where a
task alone starts a new session. Without a task, the session is told to go on.

branches is gbs run on the host: the table, or with --json the data agents read.
"""

import argparse
import http.client
import json
import os
import pathlib
import shutil
import socket
import sys

SOCKETS = (
    pathlib.Path("/run/bundle-ctl/ctl.sock"),
    pathlib.Path(os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local/state")
    / "bundle-ctl/sock/ctl.sock",
)
GO_ON = "Continue from where you left off."


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


def format_agent(agent):
    words = [agent["state"]]
    if agent["done"]:
        words.append(agent["done"])
    if agent.get("ended"):
        words.append(f"at {agent['ended']}")
    if agent.get("stale"):
        words.append("stale")
    queued, started = agent.get("queued"), agent.get("started")
    return ", ".join(
        filter(
            None,
            [
                " ".join(words),
                agent.get("model") and f"on {agent['model']}",
                queued and queued != started and f"queued {queued}",
                started and f"started {started}",
                agent.get("retry"),
                agent.get("activity"),
            ],
        ),
    )


def is_idle(row):
    agent = row["agent"]
    return row["open"] and agent and agent["state"] == "done" and agent.get("idle")


def print_status(answer):
    for row in answer["bundles"]:
        repos = ", ".join(format_repo(repo, status) for repo, status in row["repos"].items())
        if row["open"]:
            state = "open"
        elif row.get("opening"):
            state = "opening"
        else:
            state = "folder" if row["folder"] else "branch"
        if row["agent"]:
            repos += f"  [agent {format_agent(row['agent'])}]"
        print(f"{row['bundle']:60} {state:7} {repos}")
    print(f"\n{answer['agents']}/{answer['max_agents']} agents in a terminal")
    idle = [
        f"{row['bundle']} ({row['agent']['activity']})" for row in answer["bundles"] if is_idle(row)
    ]
    if answer["queue"] and answer["agents"] >= answer["max_agents"] and idle:
        print(f"the queue waits for a slot; agents done and idle, to close: {', '.join(idle)}")
    for position, item in enumerate(answer["queue"], 1):
        model = f", on {item['model']}" if item.get("model") else ""
        since = f", since {item['queued']}" if item.get("queued") else ""
        print(
            f"queued {position}: {item['bundle']} (priority {item['priority']}{model},"
            f" from {item['parent']}{since})",
        )


def print_table(answer):
    sys.stdout.write(answer["table"])


def add_task_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--task", help="run this prompt in the bundle, with Claude")
    group.add_argument("--task-file", type=pathlib.Path, help="same, read from a file")
    parser.add_argument(
        "--priority",
        type=int,
        help="with a task: higher leaves the queue first, from -100 to 100 (default 0)",
    )
    parser.add_argument(
        "--model",
        choices=("haiku", "opus", "sonnet"),
        help="with a task: the model of its agent (default opus)",
    )


def read_task(args):
    if args.task_file:
        return args.task_file.read_text()
    return args.task


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
    commands.add_parser("branches", help="gbs: every local bundle with its PR, grouped")
    create = commands.add_parser("create", help="a new bundle BASE-NAME<suffix>, branched on BASE")
    create.add_argument("base")
    create.add_argument("name")
    create.add_argument(
        "--branch-repo",
        action="append",
        dest="branch_repos",
        help="repo that gets the branch, repeatable (default: odoo)",
    )
    create.add_argument("--no-open", action="store_true")
    add_task_arguments(create)
    fetch = commands.add_parser(
        "fetch",
        help="an existing bundle from runbot or the dev remotes, or the bundle of a PR",
    )
    fetch.add_argument("name")
    fetch.add_argument("--no-open", action="store_true")
    add_task_arguments(fetch)
    open_ = commands.add_parser("open", help="a VS Code window on a local bundle")
    open_.add_argument("bundle")
    add_task_arguments(open_)
    open_.add_argument(
        "--resume",
        action="store_true",
        help="go on with the bundle's last Claude session: the task is its next prompt",
    )
    args = parser.parse_args()

    printer = None
    if args.command == "whoami":
        answer = request("GET", "/whoami")
    elif args.command == "status":
        answer = request("GET", "/status")
        printer = print_status
    elif args.command == "branches" and args.json:
        answer = request("GET", "/branches", timeout=150)
    elif args.command == "branches":
        width = shutil.get_terminal_size().columns
        answer = request("GET", f"/branches?format=table&width={width}", timeout=150)
        printer = print_table
    else:
        if args.command == "create":
            body = {"base": args.base, "name": args.name, "open": not args.no_open}
            if args.branch_repos:
                body["branch_repos"] = args.branch_repos
        elif args.command == "fetch":
            body = {"name": args.name, "open": not args.no_open}
        else:
            body = {"bundle": args.bundle}
        task = read_task(args)
        if getattr(args, "resume", False):
            body["resume"] = True
            task = task or GO_ON
        if task is not None:
            body["task"] = task
        if args.priority is not None:
            body["priority"] = args.priority
        if args.model is not None:
            body["model"] = args.model
        answer = request("POST", f"/{args.command}", body)
        if "job" in answer:
            answer = follow(answer["job"])
    if args.json or printer is None:
        print(json.dumps(answer, indent=2))
    else:
        printer(answer)


if __name__ == "__main__":
    main()
