#!/usr/bin/env python3
"""Ask the host bundle-ctl daemon for bundle work, from a dev container or from the host.

    bctl whoami
    bctl status [--json]
    bctl queue [--json]
    bctl force-queue [BUNDLE]
    bctl move BUNDLE PLACE
    bctl requeue BUNDLE [--paused | --github]
    bctl schedule BUNDLE spot|manual|HH:MM
    bctl unqueue BUNDLE
    bctl branches [--json]
    bctl create BASE NAME [--branch-repo REPO]... [--no-open] [TASK]
    bctl fetch BUNDLE|PR_LINK|OWNER:BRANCH [--no-open] [TASK]
    bctl open BUNDLE [TASK] [--resume | --terminal] [--wait SECONDS]
    bctl open --cut [--resume [--wait SECONDS]]
    bctl build
    bctl delete [BUNDLE] --reason TEXT [--drop COMMIT]... [--wait SECONDS]
    bctl run [--kind KIND] [--workers N] [-- COMMAND...]
    bctl runs [--json]

TASK is --task TEXT or --task-file FILE, with an optional --priority N, --after HH:MM and
--paused.

create, fetch and open run as a job on the host: bctl prints its log until it ends, which can take
minutes for a new bundle. With a task, create and fetch only queue it, and no window opens: when
its turn comes, its agent starts in the bare folder of the bundle, with no worktree, and runs
`bctl build` once the task needs the code (see build). Once the bundle fits under the cap of
running bundles, Claude runs the task in a terminal of the host, in the bundle's container, and a
VS Code window opened on the bundle later takes the session over in a Claude tab. A bundle whose
window is already open runs the task in that window. A task for a bundle whose agent has not ended
is queued behind it. The queue runs the highest priority first (-100 to 100; by default 0 from the
host, and from an agent the priority of its own task), then in arrival order: bctl status lists it
in that order, with what each task waits for. One agent
starts at a time: the next task waits until the run of the last one began. --after keeps a task
in the queue until that time of the host comes, today or tomorrow, and --paused until it is
scheduled again: it waits for a manual resume.

queue is that list alone, at once, with the memory left on the host. force-queue lets the tasks
queued now start over the cap, those that wait for their time or for a manual resume aside, or
the one of BUNDLE, its time and its pause too, still one at a time; a task queued later waits for
a slot again. Only the host may ask it: a container gets a 403.

move puts the queued task of a bundle at PLACE in the queue, from 1: it keeps its priority when
that priority fits between the tasks around, else takes the nearest of theirs. schedule sets when
the queued task of a bundle may start: spot, as soon as a slot and the memory let it; manual, once
it is scheduled again; HH:MM, once that time of the host came, today or tomorrow. It no longer goes
over the cap. unqueue drops the queued task of a bundle. The host, that bundle, or the bundle that
queued the task may ask them.

requeue queues the session of a bundle's agent again, last, and closes its agent window once its
turn ended, 2 minutes at most: meanwhile its Bash calls are refused, on a reason that tells it to
end its turn. A turn cut there goes on told so. With --paused, it waits for a manual resume. Only
the host may ask it, but for --github: an agent that GitHub cannot reach asks it for itself, its
session waits until the GitHub connection of the host is up, and goes on told so.

open --resume gives the task to the last Claude session of the bundle as its next prompt, where a
task alone starts a new session. Without a task, the session is told to go on.

open --terminal opens the agent terminal of the bundle again, on its last Claude session, with
no prompt: after the X server took the windows away, for one. It is refused while a terminal holds
that session. When a VS Code window holds it, the window closes and the terminal goes on with the
session, minimized: only the bundle itself or the host may ask it.

open ops opens the VS Code window of the ops container, and with a task or --terminal a new Claude
session of that container in an agent window of its own, at once. open ops-SESSION --terminal goes
on with that session in an agent window, once nothing runs it: a session moved from a tab of the
ops window. open ops-SESSION alone closes the agent window of that session and goes on with it in a
tab of the ops window. Only the host and the ops container may ask them, but for open ops with a
task alone from a bundle: it waits for Seb in hok, whose yes opens the ops agent on it. --wait
then works as for delete, and the same task sent again takes back the request that waits.

open --cut lists the Claude sessions a crash or a restart cut in the last 3 days, bundles and ops:
no claude runs them, and their last turn had not ended. With --resume it asks Seb in hok to resume
them all, behind one yes: each ops session goes on in an ops agent window, each bundle session in
its agent terminal through the queue, on a prompt that says what stopped it and what to check
before its last call runs again. --wait then works as for delete. Only the host and the ops
container may ask it.

build makes the bundle of this container, whose agent started in its bare folder: the create or
fetch its task was queued with runs now, and the worktrees appear in /workspace.

branches is gbs run on the host: the table, or with --json the data agents read.

delete asks Seb in hok to delete a bundle, the bundle of this container when none is named (a
container asks for its own, or for a bundle whose last task it queued). It is refused when a
branch of the bundle has a commit no remote has, or is pushed. --drop COMMIT names a commit of Seb
no remote has that the deletion may lose: hok lists it, and his yes loses it. His yes runs
delete_bundle once the container of the bundle stopped. When the session of its agent window ended
on its report, hok shows the report and the yes closes the window. With --wait, bctl waits that
long for his answer and prints it: exit 0 on his yes, 126 when he refused, 75 when it still waits.
The same delete asked again takes back the request that waits.

run waits in the run queue of the host until cores and memory are free for a heavy command, then
runs COMMAND pinned to the cores it got, which it holds until COMMAND ends. Without COMMAND the
run is the calling shell: bctl returns once it may go on, and the cores are held until that shell
ends. A hook of the bundles starts every test run, install and server of an agent this way. When
the daemon does not answer, the command runs without the queue. runs lists what runs and waits.
"""

import argparse
import http.client
import json
import os
import pathlib
import shutil
import socket
import sys
import time

RUN_ANSWER = 60
RUN_RETRY = 60
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
                agent.get("effort") and f"effort {agent['effort']}",
                queued and queued != started and f"queued {queued}",
                started and f"started {started}",
                agent.get("retry"),
                agent.get("activity"),
            ],
        ),
    )


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
    print(f"\n{answer['agents']}/{answer['max_agents']} agents at work in a terminal")
    print_queued(answer["queue"])
    runs = answer.get("runs")
    if runs and (runs["running"] or runs["queue"]):
        print(f"{len(runs['running'])} runs, {len(runs['queue'])} queued: bctl runs")


def print_queued(queue):
    for position, item in enumerate(queue, 1):
        asked = f", from {item['after']}" if item.get("after") else ""
        asked += ", over the cap" if item.get("forced") else ""
        asked += f", on {item['model']}" if item.get("model") else ""
        asked += f", effort {item['effort']}" if item.get("effort") else ""
        asked += ", not built yet" if not item.get("built", True) else ""
        since = f", since {item['queued']}" if item.get("queued") else ""
        since += f", waits for {item['waits_for']}" if item.get("waits_for") else ""
        print(
            f"queued {position}: {item['bundle']} (priority {item['priority']}{asked},"
            f" from {item['parent']}{since})",
        )


def format_room(memory):
    room = memory.get("room")
    return "" if room is None else f"{room} GB left under the limit of the containers, "


def format_memory(memory):
    """What the host has left, as one line that starts with a warning when it is short of it."""
    figures = (
        f"{memory['available']} GB available, {format_room(memory)}{memory['swap']} GB in swap,"
        f" programs stalled {memory['stall']:.0f}% of the last minute"
    )
    if memory.get("ramping"):
        figures += f", {memory['ramping']} agents started in the last 10 minutes"
    return f"the host is short of memory: {figures}" if memory["short"] else f"memory: {figures}"


def print_requeued(answer):
    print(
        f"{answer['bundle']}: its window closes once its turn ends, in {answer['wait']} seconds at"
        f" most; queued {answer['queued']}, waits for {answer['waits_for']}",
    )


def print_queue(answer):
    if answer.get("forced"):
        count = len(answer["forced"])
        print(f"{count} queued task{'s' * (count != 1)} may start over the cap, one at a time")
        for bundle in answer["started"]:
            print(f"started: {bundle}")
    print(f"{answer['agents']}/{answer['max_agents']} agents at work in a terminal")
    if not answer["queue"]:
        print("no task is queued")
    print_queued(answer["queue"])
    print(format_memory(answer["memory"]))


def print_deletion(answer):
    stop = "its agent window closed" if answer.get("closes") else "its container stopped"
    print(f"waits for Seb: hok {answer['held']}. His yes deletes {answer['bundle']} once {stop}.")


def print_ops_task(answer):
    print(f"waits for Seb: hok {answer['held']}. His yes opens an ops agent on this task.")


def print_cut(answer):
    for row in answer["sessions"]:
        print(row["name"] + (f"  {row['title']}" if row["title"] not in ("", row["name"]) else ""))
        print(f"  cut {row['time']}: {row['why']}")
        if row["call"]:
            print(f"  last call: {row['call']}")
        print(f"  last prompt: {row['prompt']}")
    count = len(answer["sessions"])
    sessions = f"{count} session{'s' * (count != 1)}"
    if answer.get("held"):
        print(f"waits for Seb: hok {answer['held']}. His yes resumes these {sessions}.")
    elif count:
        print(f"{sessions} cut in the last {answer['days']} days: bctl open --cut --resume")
    else:
        print(f"no session was cut in the last {answer['days']} days")


def print_table(answer):
    sys.stdout.write(answer["table"])


def print_unqueued(answer):
    print(
        f"dropped the task of {answer['bundle']}, from {answer['parent']} since {answer['queued']}",
    )


def format_span(seconds):
    if seconds < 90:
        return f"{round(seconds)} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


def format_cores(count):
    return "1 core" if count == 1 else f"{count} cores"


def print_runs(answer):
    now = time.time()
    for run in answer["running"]:
        cores = format_cores(run["need"]) if run["need"] else "shared"
        cores = "lent" if run["lent"] else cores
        span = format_span(now - run["started"])
        print(f"running   {run['bundle']:50} {run['kind']:6} {cores:7} {span:>7}  {run['label']}")
    for place, run in enumerate(answer["queue"], 1):
        cores, span = format_cores(run["need"]), format_span(now - run["queued"])
        print(
            f"queued {place:<2} {run['bundle']:50} {run['kind']:6} {cores:7} {span:>7}"
            f"  {run['label']}",
        )
    if answer["running"] or answer["queue"]:
        print()
    print(
        f"{answer['cores'] - answer['free']}/{answer['cores']} cores taken (CPUs {answer['cpus']}),"
        f" {answer['available']} GB of memory available, {format_room(answer)}"
        f"{answer['min_free']:g} GB kept free",
    )


def print_place(place):
    now = time.time()
    ahead = ", ".join(
        f"{run['bundle']} ({run['kind']}, {format_span(now - run['started'])})"
        for run in place["ahead"]
    )
    what = "memory on the host" if place["blocker"] == "memory" else format_cores(place["need"])
    print(
        f"bctl run: waits for {what}, place {place['place']} in the queue."
        + (f" Running: {ahead}." if ahead else ""),
        file=sys.stderr,
    )


def wait_for_cores(body):
    """Queue a run and print where it waits: the daemon's answer once it starts, or None and why
    when the queue does not answer.
    """
    told, deadline = None, None
    while True:
        socket_path = next((path for path in SOCKETS if path.exists()), None)
        if socket_path is None:
            return None, "no bundle-ctl socket"
        connection = UnixConnection(socket_path, RUN_ANSWER)
        headers = {"Content-Type": "application/json"}
        try:
            connection.request("POST", "/run", body=json.dumps(body).encode(), headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                error = json.loads(response.read() or b"{}").get("error", "")
                return None, f"{response.status} {error}".strip()
            for line in response:
                answer, deadline = json.loads(line), None
                if "cpus" in answer:
                    return answer, None
                if answer != told:
                    if told is None and os.environ.get("CLAUDECODE"):
                        print(
                            "bctl run: a foreground call that outlives its timeout goes to the"
                            " background, and is stopped 10 minutes later: start a long run in"
                            " the background.",
                            file=sys.stderr,
                        )
                    told = answer
                    print_place(answer)
            reason = "the daemon closed the connection"
        except (OSError, ValueError, http.client.HTTPException) as error:
            reason = str(error) or type(error).__name__
        finally:
            connection.close()
        deadline = deadline or time.monotonic() + RUN_RETRY
        if time.monotonic() > deadline:
            return None, reason
        time.sleep(3)


def run(args):
    command = args.run_command[1:] if args.run_command[:1] == ["--"] else args.run_command
    body = {
        "kind": args.kind,
        "label": args.label or " ".join(command),
        "shell": not command,
        "workers": args.workers,
    }
    answer, reason = wait_for_cores(body)
    if answer is None:
        print(f"bctl run: no run queue ({reason}), running without it", file=sys.stderr)
    else:
        if answer["waited"] >= 5:
            cores = f"on {format_cores(answer['need'])}" if answer["need"] else "on shared cores"
            print(
                f"bctl run: started after {format_span(answer['waited'])}, {cores}",
                file=sys.stderr,
            )
        try:
            os.sched_setaffinity(0 if command else os.getppid(), answer["cpus"])
        except OSError as error:
            print(f"bctl run: not pinned to its cores: {error}", file=sys.stderr)
    if command:
        try:
            os.execvp(command[0], command)
        except OSError as error:
            sys.exit(f"bctl run: {command[0]}: {error.strerror}")


def add_task_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--task", help="run this prompt in the bundle, with Claude")
    group.add_argument("--task-file", type=pathlib.Path, help="same, read from a file")
    parser.add_argument(
        "--after",
        metavar="HH:MM",
        help="with a task: it starts no sooner than this time of the host, today or tomorrow",
    )
    parser.add_argument(
        "--paused",
        action="store_true",
        help="with a task: it waits in the queue until `bctl schedule BUNDLE spot`",
    )
    parser.add_argument(
        "--priority",
        type=int,
        help="with a task: higher leaves the queue first, from -100 to 100 (default: the priority"
        " of this agent's own task, 0 from the host)",
    )
    parser.add_argument(
        "--model",
        choices=("haiku", "opus", "sonnet"),
        help="with a task: the model of its agent (default opus)",
    )
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh"),
        help="with a task: the effort of its agent (default medium)",
    )


def read_task(args):
    if args.task_file:
        return args.task_file.read_text()
    return args.task


def wait_held(held, seconds):
    deadline = time.monotonic() + seconds
    while True:
        wait = max(0, min(50, round(deadline - time.monotonic())))
        answer = request("GET", f"/held?id={held}&wait={wait}", timeout=70)
        if answer["state"] != "waits" or not wait:
            break
    print(answer["text"])
    sys.exit({"refused": 126, "sent": 0, "waits": 75}.get(answer["state"], 1))


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
    commands.add_parser("queue", help="the agent tasks that wait, and the memory left on the host")
    force = commands.add_parser("force-queue", help="let the tasks queued now start over the cap")
    force.add_argument("bundle", nargs="?", help="default: every task queued now")
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
    open_.add_argument("bundle", nargs="?")
    add_task_arguments(open_)
    open_.add_argument(
        "--cut",
        action="store_true",
        help="no bundle: the sessions a crash or a restart cut, with --resume asked in hok",
    )
    open_.add_argument(
        "--resume",
        action="store_true",
        help="go on with the bundle's last Claude session: the task is its next prompt",
    )
    open_.add_argument(
        "--terminal",
        action="store_true",
        help="the agent terminal on the bundle's last Claude session, with no prompt",
    )
    open_.add_argument(
        "--wait",
        default=0,
        type=int,
        help="ops with a task from a bundle, or --cut: seconds to wait for Seb's answer",
    )
    commands.add_parser("build", help="make the bundle of this container, started bare")
    add_repo = commands.add_parser(
        "add-repo",
        help="the worktree of a repo the bundle was made without (design-themes, documentation)",
    )
    add_repo.add_argument("repo")
    add_repo.add_argument("bundle", nargs="?", help="default: the bundle of this container")
    delete = commands.add_parser("delete", help="ask Seb in hok to delete a bundle")
    delete.add_argument("bundle", nargs="?", help="default: the bundle of this container")
    delete.add_argument(
        "--drop",
        action="append",
        default=[],
        help="a commit of Seb no remote has that the deletion may lose, listed in hok",
        metavar="COMMIT",
    )
    delete.add_argument("--reason", required=True, help="the line Seb reads before his yes")
    delete.add_argument("--wait", default=0, type=int, help="seconds to wait for his answer")
    run_ = commands.add_parser("run", help="a heavy command, once the host has cores for it")
    run_.add_argument(
        "--kind",
        choices=("hoot", "odoo", "other", "server", "shell"),
        default="other",
        help="what runs: a server and a shell share the cores and never wait (default other)",
    )
    run_.add_argument("--label", help="what `bctl runs` shows (default: the command)")
    run_.add_argument("--workers", default=1, type=int, help="the workers of a hoot run")
    run_.add_argument("run_command", metavar="COMMAND", nargs=argparse.REMAINDER)
    move = commands.add_parser("move", help="put the queued task of a bundle at another place")
    move.add_argument("bundle")
    move.add_argument("place", type=int, help="from 1, as bctl queue counts")
    requeue = commands.add_parser(
        "requeue",
        help="queue the session of a bundle's agent again, and close its window after its turn",
    )
    requeue.add_argument("bundle")
    waits = requeue.add_mutually_exclusive_group()
    waits.add_argument(
        "--paused",
        action="store_true",
        help="it waits in the queue until `bctl schedule BUNDLE spot`",
    )
    waits.add_argument(
        "--github",
        action="store_true",
        help="it waits in the queue until the GitHub connection of the host is up",
    )
    schedule = commands.add_parser("schedule", help="set when the queued task of a bundle starts")
    schedule.add_argument("bundle")
    schedule.add_argument(
        "when",
        help="spot: once a slot is free, manual: once scheduled again, HH:MM: at that time",
    )
    commands.add_parser("runs", help="the heavy commands that run and the ones that wait")
    unqueue = commands.add_parser("unqueue", help="drop the queued task of a bundle")
    unqueue.add_argument("bundle")
    args = parser.parse_args()
    if args.command == "open" and (
        bool(args.bundle) == args.cut or (args.cut and (args.terminal or read_task(args)))
    ):
        parser.error("open: a BUNDLE, or --cut with no task nor --terminal")

    printer = None
    if args.command == "run":
        return run(args)
    if args.command == "runs":
        answer = request("GET", "/runs")
        printer = print_runs
    elif args.command == "whoami":
        answer = request("GET", "/whoami")
    elif args.command == "status":
        answer = request("GET", "/status")
        printer = print_status
    elif args.command == "queue":
        answer = request("GET", "/queue")
        printer = print_queue
    elif args.command == "force-queue":
        answer = request("POST", "/force-queue", {"bundle": args.bundle} if args.bundle else {})
        printer = print_queue
    elif args.command == "move":
        answer = request("POST", "/move", {"bundle": args.bundle, "place": args.place})
        printer = print_queue
    elif args.command == "requeue":
        body = {"bundle": args.bundle, "paused": args.paused}
        answer = request("POST", "/requeue", {**body, "github": True} if args.github else body)
        printer = print_requeued
    elif args.command == "schedule":
        answer = request("POST", "/schedule", {"bundle": args.bundle, "when": args.when})
        printer = print_queue
    elif args.command == "unqueue":
        answer = request("POST", "/unqueue", {"bundle": args.bundle})
        printer = print_unqueued
    elif args.command == "build":
        answer = follow(request("POST", "/build", {})["job"])
    elif args.command == "add-repo":
        answer = request("POST", "/add-repo", {"bundle": args.bundle, "repo": args.repo})
        answer = follow(answer["job"])
    elif args.command == "delete":
        answer = request(
            "POST",
            "/delete",
            {"bundle": args.bundle, "drop": args.drop, "reason": args.reason},
        )
        printer = print_deletion
        if args.wait:
            print_deletion(answer)
            sys.stdout.flush()
            wait_held(answer["held"], args.wait)
    elif args.command == "open" and args.cut:
        if args.resume:
            answer = request("POST", "/open", {"cut": True, "resume": True})
        else:
            answer = request("GET", "/cut")
        printer = print_cut
        if answer.get("held") and args.wait:
            print_cut(answer)
            sys.stdout.flush()
            wait_held(answer["held"], args.wait)
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
        if getattr(args, "terminal", False):
            body["terminal"] = True
        if getattr(args, "resume", False):
            body["resume"] = True
            task = task or GO_ON
        if task is not None:
            body["task"] = task
        if args.after is not None:
            body["after"] = args.after
        if args.paused:
            body["paused"] = True
        if args.priority is not None:
            body["priority"] = args.priority
        if args.model is not None:
            body["model"] = args.model
        if args.effort is not None:
            body["effort"] = args.effort
        answer = request("POST", f"/{args.command}", body)
        if "job" in answer:
            answer = follow(answer["job"])
        if "held" in answer:
            printer = print_ops_task
            if args.wait:
                print_ops_task(answer)
                sys.stdout.flush()
                wait_held(answer["held"], args.wait)
    if args.json or printer is None:
        print(json.dumps(answer, indent=2))
    else:
        printer(answer)


if __name__ == "__main__":
    main()
