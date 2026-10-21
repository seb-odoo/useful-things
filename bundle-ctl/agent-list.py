#!/usr/bin/python3
"""A window that stays open on the rows of window-picker.py and on the queue of the agents, read
again every 2 seconds, the queue every 10.

    agent-list.py

The rows are the ones of the picker, the terminals shown on screen included: each with its mark, its
kind, its bundle or the title of its conversation, the state of its agent and since when, or the
error its window shows once its claude ended on it (❌ for the picker), ", shown"
when its terminal is not minimized, its CPU, RAM and disk, the icon of each Firefox tab group of a
bundle, then why they look finished when they all do (tab_groups.py), and the close line of the
last answer of its session (picker.get_close_line), else the first line of the result.md of a
bundle. A click on a column header sorts the rows by that column: by mark in the order of the picker, by
kind in its order, by time, by number, or by text. CPU and RAM are those of the container of a
bundle, its file cache aside, or of the claude of an ops window and its children, as the ops agents share
one container; read every 5 seconds, 100% is one core. Disk is the folder of a bundle, measured
with du when its row comes, then every 10 minutes.

The buttons above the rows count the rows of each mark among the kinds pressed, and of each kind
among the marks pressed. Pressed, they keep the rows of one of the marks pressed and of one of the
kinds pressed. The Branch search beside them keeps the rows whose name has its text, and the
tasks of the queue whose bundle has it, case and a dev remote prefix aside; the buttons count
only the rows it keeps, and Up and Down move a task past the one shown above or below it. Enter,
a double click or Terminal brings up the terminal of each selected row (Ctrl+click, Shift+click)
where it was, the first row last, so that it ends in front. VS Code opens the window of each
selected bundle, which takes its session over. Show tabs brings up the Firefox tab group of the
first selected bundle, and Close tabs closes the groups of each, at once. Close closes the window
of each selected row after a question, as the daemon does: SIGUSR1 to its agent-terminator.py,
and its claude stops.
Back to queue queues the session of each selected bundle again, at the end of the queue (the lowest
priority there), and the daemon closes its window once its turn ended, 2 minutes
at most (bctl requeue): the queue starts it again with "Continue from where you left off." when its
turn comes. Pause does the same, and the task waits in the queue for a manual resume. A right
click on a row opens a menu with these seven. New ops agent runs bctl open ops --terminal.

A task of the queue has its own state (paused, over the cap, else queued) and the kind of its
bundle: that of its row when it has one, else the one its window would show once the session it
goes on with is idle. Its last message is its own
text, or the last prompt of that session for a session queued again as it was, and its last answer
the close line of the last answer of that session, else its first line. Under
the queue, Force lets each selected task start over the cap, Up and Down move the first selected
task one place, Pause makes each selected task wait for a manual resume, Resume for a slot, and
Time for a time of the day it asks (bctl schedule); a right click on a task opens a menu with these
six. A second start brings the open window up. The
size and the place of the window, and the height of the queue under its handle, are kept in
agent-list.json, beside the state of the daemon.

These parts are Agents, which another window can show with rows of its own under each agent (a
Child each) and rows of its own at the end. When host-ctl is on this machine, a start opens the
window of hok (hokwin.py), which shows them with the requests of each agent under its row; this
window opens alone when hok ends with an error within START seconds. There, the 🔑 hok button
before the marks keeps the agents with a request under them and the groups of requests, besides
the rows of the marks pressed, and counts those requests.

    agent-list.py --select KEY

brings the window up on the row of KEY once its rows show it: a bundle, an ops agent window by
its session (ops-SESSION), or a row of the owner, a request of hok (hok:NAME). The band of an
agent terminal links it through bundle_open.py.
"""

import collections
import contextlib
import importlib.util
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk, Pango  # noqa: F401

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import tab_groups  # noqa: E402
from agents import OPS, get_agent_folder, get_mtime, read_text  # noqa: E402
from commands import clean_bundle_name, get_worktree_bundle_folder  # noqa: E402
from config import REPO_ROOT  # noqa: E402

AGENTS_CHECK = 2
APPEARANCE = ("org.freedesktop.appearance", "color-scheme")
ASKS_SEB = ("failed", "finished", "waiting")
BUNDLE = 2
Child = collections.namedtuple("Child", "key state kind name since answer age")
COLUMNS = ("State", "Type", "Agent", "Since", "CPU", "RAM", "Disk", "Tabs", "Last answer")
CONTAINERS = pathlib.Path(
    f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/user.slice"
)
DISK_AGE = 600
DISK_CHECK = 60
HOK = pathlib.Path(REPO_ROOT) / "host-ctl" / "hokwin.py"
ICON = HERE / "agent-list.svg"
KEY, WINDOW, TITLE = 9, 10, 11
NOT_BUILT = "Nothing of this bundle is checked out yet"
OWNED = "🔑 hok"
PICK_TIME = 10
SORTS = (12, 13, 2, 14, 15, 16, 17, 7, 8)
PLACE = (
    pathlib.Path(os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local/state")
    / "bundle-ctl"
    / "agent-list.json"
)
QUEUE_CHECK = 10
QUEUE_COLUMNS = (
    "State",
    "Type",
    "Queued",
    "Priority",
    "Since",
    "Waits for",
    "Last message",
    "Last answer",
)
SCOPE = re.compile(r"libpod-([0-9a-f]{64})\.scope")
START = 3
USAGE_CHECK = 5
WIDTH = 40


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bctl = load("bctl", HERE / "client" / "bctl.py")
picker = load("picker", HERE / "window-picker.py")
runs = load("runs", HERE / "runs.py")
ICONS = {kind: icon for icon, kind, _ in load("title", HERE / "client" / "window-title.py").KINDS}

sessions = {}


def get_info(row, close):
    """The state of the agent of a row and since when, from the .agent/ files of its bundle, the
    close line of its last answer, else the first line of its result.md, and that since as a time,
    0 when none. An ops row has its window as key, and no such files. A window whose claude ended
    and that waits for Enter has the error it shows as state."""
    shown = [] if row.minimized else ["shown"]
    if row.key == row.window:
        return ", ".join(filter(None, [row.ended, *shown])), close, 0.0
    folder = get_agent_folder(row.key)
    now = time.time()
    if (since := get_mtime(folder / "done")) is not None:
        word = "failed" if read_text(folder / "done") == "failed" else "done"
        state = f"{word} {bctl.format_span(now - since)} ago"
    elif (since := get_mtime(folder / "waiting")) is not None:
        state = f"waiting for {bctl.format_span(now - since)}"
    elif (since := get_mtime(folder / "run.lock")) is not None:
        state = f"running for {bctl.format_span(now - since)}"
    else:
        state = "starting"
    answer = close or (read_text(folder / "result.md") or "").partition("\n")[0]
    return ", ".join([row.ended or state, *shown]), answer, since or 0.0


def get_tabs(groups):
    """The icon of each Firefox tab group of a bundle, then why they look finished when they all
    do."""
    icons = "".join(group["title"].partition(" ")[0] for group in groups)
    reasons = sorted({group.get("finished") or "" for group in groups})
    return f"{icons} {', '.join(reasons)}" if groups and all(reasons) else icons


def read_agents():
    """Each row of the picker with its state, its close line, its tabs and its session."""
    rows = picker.get_rows(picker.get_windows(), shown=True)
    pids = picker.read_window_pids() if any(row.key == row.window for row in rows) else {}
    found = {row.key: picker.get_session(row, pids) for row in rows}
    groups = tab_groups.get_groups()
    return [
        (
            row,
            (
                *get_info(row, picker.get_close_line(found[row.key])),
                get_tabs(groups.get(row.key, [])),
                found[row.key],
            ),
        )
        for row in rows
    ]


def get_kind(row):
    order = picker.get_order(row.title)
    return picker.KINDS[order] if order < len(picker.KINDS) else None


def get_mark_and_kind(row):
    """The State and Type columns of a row."""
    kind = get_kind(row)
    return [
        " ".join(picker.MARKS[row.rank]) if row.rank < len(picker.MARKS) else "",
        f"{ICONS[kind]} {kind}" if kind else "",
    ]


def get_name(row):
    """The title of a row without its mark, its icon and its kind, which have columns of their
    own."""
    loading = row.title.startswith(picker.LOADING)
    title = row.title.removeprefix(f"{picker.LOADING} ")
    if row.rank < len(picker.MARKS):
        title = title.removeprefix(f"{picker.MARKS[row.rank][0]} ")
    if kind := get_kind(row):
        title = title.removeprefix(f"{ICONS[kind]} ").removesuffix(f" ({kind})")
    return f"{picker.LOADING} {title}" if loading else title


def get_filters(rows, ranks, kinds, search="", owned=None):
    """The label of each filter button: each mark, then each kind, with its number of rows among
    the ones the search and the pressed buttons of the other line keep, owned as for is_kept."""
    found = [get_kind(row) for row in rows if is_kept(row, ranks, set(), search, owned)]
    labels = [f"{ICONS[kind]} {kind} {found.count(kind)}" for kind in picker.KINDS]
    kept = [row for row in rows if is_kept(row, set(), kinds, search)]
    return [*picker.get_counts(kept), *labels]


def is_found(text, search):
    return clean_bundle_name(search).strip().casefold() in text.casefold()


def is_kept(row, ranks, kinds, search="", owned=None):
    """Whether a row has the search in its name, one of the kinds, and one of the marks of ranks
    or its key in owned, the agents with rows of the owner under them while its button is pressed,
    None when not. The buttons of a line add up, and a line with none pressed keeps all."""
    if not is_found(get_name(row), search):
        return False
    marked = row.rank in ranks or row.key in (owned or ())
    return (marked or not ranks and owned is None) and (not kinds or get_kind(row) in kinds)


def get_window_title(rows):
    """The title of the window, with how many agents wait for Seb, failed, finished or waiting:
    the window list of the panel shows it."""
    words = [picker.MARKS[row.rank][1] for row in rows if row.rank < len(picker.MARKS)]
    count = sum(word in ASKS_SEB for word in words)
    return f"Agent list ({count})" if count else "Agent list"


def format_size(size):
    return f"{size / runs.GIGABYTE:.1f} GB"


def close_windows(windows):
    """Close agent windows as the daemon does, so that their claude stops. Another window closes as
    by its close button."""
    pids = picker.read_window_pids()
    for window in windows:
        if terminator := picker.get_terminator(pids.get(window)):
            with contextlib.suppress(ProcessLookupError):
                os.kill(terminator[0], signal.SIGUSR1)
        else:
            subprocess.run(["wmctrl", "-i", "-c", window], check=False)


def read_cgroup(path):
    """The CPU seconds of a cgroup, and the memory it holds, its file cache aside."""
    try:
        stat = dict(line.split() for line in (path / "memory.stat").read_text().splitlines())
        current = int((path / "memory.current").read_text())
    except OSError:
        return None
    cpu = runs.read_cpu_seconds(path)
    return cpu is not None and (cpu, current - int(stat["file"]) + int(stat["shmem"]))


def read_processes():
    """Each process: its parent, its CPU seconds with those of the children it waited for, its
    resident memory and its arguments."""
    tick, page = os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE")
    processes = {}
    for path in pathlib.Path("/proc").glob("[0-9]*"):
        try:
            fields = (path / "stat").read_text().rpartition(")")[2].split()
            args = (path / "cmdline").read_text(errors="replace").split("\0")
        except OSError:
            continue
        cpu = sum(int(value) for value in fields[11:15]) / tick
        processes[int(path.name)] = (int(fields[1]), cpu, int(fields[21]) * page, args)
    return processes


def read_tree(processes, session):
    """The CPU seconds and the resident memory of the claude of a session and its children, None
    when it does not run."""
    children = collections.defaultdict(list)
    for pid, (parent, *_rest) in processes.items():
        children[parent].append(pid)
    todo = [
        pid
        for pid, (*_rest, args) in processes.items()
        if pathlib.Path(args[0]).name == "claude" and session in args
    ]
    if not todo:
        return None
    cpu = memory = 0
    while todo:
        pid = todo.pop()
        cpu, memory = cpu + processes[pid][1], memory + processes[pid][2]
        todo += children[pid]
    return cpu, memory


class Usage:
    """The CPU, the RAM and the disk of the rows of keys, (key, window) pairs, by key."""

    def __init__(self):
        self.disks = {}
        self.folders = {}
        self.keys = []
        self.samples = {}

    def get_containers(self):
        """The cgroup of each running container, by its folder. podman tells the folder of the
        containers it did not see yet."""
        scopes = {}
        for path in CONTAINERS.glob("libpod-*.scope"):
            if match := SCOPE.fullmatch(path.name):
                scopes[match[1]] = path
        if not scopes.keys() <= self.folders.keys():
            labels = '{{.ID}} {{index .Labels "devcontainer.local_folder"}}'
            command = ["podman", "ps", "--no-trunc", "--format", labels]
            out = subprocess.run(command, capture_output=True, check=False, text=True).stdout
            self.folders = dict(line.partition(" ")[::2] for line in out.splitlines())
        return {self.folders[id_]: path for id_, path in scopes.items() if self.folders.get(id_)}

    def read(self):
        """The CPU since the last read, in % of a core and None on a first read, and the RAM of
        each row."""
        now, rows = time.monotonic(), self.keys
        containers = self.get_containers()
        pids = picker.read_window_pids() if any(key == window for key, window in rows) else {}
        processes = read_processes() if pids else {}
        samples, found = {}, {}
        for key, window in rows:
            if key != window:
                cgroup = containers.get(get_worktree_bundle_folder(key))
                sample = cgroup and read_cgroup(cgroup)
            else:
                terminator = picker.get_terminator(pids.get(window))
                sample = terminator and read_tree(processes, terminator[1].removeprefix(OPS))
            if not sample:
                continue
            samples[key] = (sample[0], now)
            before = self.samples.get(key)
            cpu = before and max(0, sample[0] - before[0]) / (now - before[1]) * 100
            found[key] = (cpu, sample[1])
        self.samples = samples
        return found

    def read_disks(self):
        """The disk of the folder of each bundle of the rows, measured again after DISK_AGE."""
        for key, window in self.keys:
            measured = self.disks.get(key)
            if key == window or (measured and time.monotonic() - measured[1] < DISK_AGE):
                continue
            command = ["du", "-s", "-k", get_worktree_bundle_folder(key)]
            out = subprocess.run(command, capture_output=True, check=False, text=True).stdout
            size = out.split()[:1]
            self.disks[key] = (int(size[0]) * 1024 if size else None, time.monotonic())
        return {key: size for key, (size, _at) in self.disks.items()}


def ask(method, path, body=None):
    """The answer of the daemon to a request, or the reason it gave none, as an error."""
    socket_path = next((path for path in bctl.SOCKETS if path.exists()), None)
    if socket_path is None:
        return {"error": "bundle-ctl does not answer"}
    connection = bctl.UnixConnection(socket_path, QUEUE_CHECK)
    data = json.dumps(body).encode() if body is not None else None
    try:
        connection.request(method, path, body=data)
        response = connection.getresponse()
        answer = json.loads(response.read() or b"{}")
    except (OSError, ValueError) as error:
        return {"error": f"bundle-ctl: {error}"}
    finally:
        connection.close()
    if response.status >= 300:
        return {"error": f"{response.status} {answer.get('error', '')}"}
    return answer


def get_last_prompt(path):
    """The last prompt someone wrote in a transcript, a resume of the daemon aside, on one line and
    without the note of a bundle not built yet."""
    prompt = ""
    with path.open("rb") as file:
        for line in file:
            if b'"type":"user"' not in line:
                continue
            try:
                entry = json.loads(line)
                content = entry["message"]["content"]
                if isinstance(content, list):
                    content = "\n".join(part["text"] for part in content if part["type"] == "text")
                origin = (entry.get("origin") or {}).get("kind", "human")
            except (KeyError, TypeError, ValueError):
                continue
            text = content.strip()
            if entry.get("isMeta") or origin != "human" or not text:
                continue
            if text.startswith((bctl.GO_ON, "<", "[Request interrupted")):
                continue
            if text.startswith(NOT_BUILT):
                text = text.partition("\n\n")[2]
            prompt = " ".join(text.split())
    return prompt


def read_session(bundle, session):
    """The title the window of a bundle shows once its session is idle, the last prompt of that
    session, and the close line of its last answer, else its first line. Read again only once
    the transcript changed, as the queue is read every 10 seconds."""
    title = picker.load_title()
    path = session and title.get_transcript(session)
    try:
        stat = path.stat()
    except (AttributeError, OSError):
        return title.get_title(bundle), "", ""
    stamp = (stat.st_mtime_ns, stat.st_size)
    if sessions.get(session, (None,))[0] != stamp:
        last = title.get_last_answer(session)
        first = next((line.strip() for line in last.splitlines() if line.strip()), "")
        found = (
            title.get_title(bundle, {session: "idle"}),
            get_last_prompt(path),
            title.get_close_line(last) or first,
        )
        sessions[session] = (stamp, found)
    return sessions[session][1]


def read_queue():
    """The answer of GET /queue, None when the daemon does not answer. Each task gets the title,
    the last message and the last answer of the session it goes on with: the one it names, else
    the one of its bundle when it resumes it. A new task has its own text as last message."""
    answer = ask("GET", "/queue")
    if "error" in answer:
        return None
    for item in answer["queue"]:
        session = item.get("session") or (
            item.get("resume") and read_text(get_agent_folder(item["bundle"]) / "session")
        )
        title, prompt, last = read_session(item["bundle"], session)
        task = item.get("task") or ""
        message = prompt if task == bctl.GO_ON else " ".join(task.split())
        item.update(title=title, message=message, answer=last)
    return answer


def describe_queue(answer, rows=(), search=""):
    """The head and the lines of the queue, those of the bundles the search is in. A task whose
    bundle has a row takes its kind from it, else from the title read with the queue."""
    if answer is None:
        return "bundle-ctl does not answer", []
    head = f"{answer['agents']}/{answer['max_agents']} agents at work in a terminal"
    head += f", {len(answer['queue'])} queued"
    if answer["memory"]["short"]:
        head += f"; {bctl.format_memory(answer['memory'])}"
    found = {row.key: row for row in rows}
    lines = []
    for item in answer["queue"]:
        if not is_found(item["bundle"], search):
            continue
        title = item.get("title") or ""
        row = found.get(item["bundle"])
        row = row or picker.Row(None, picker.get_rank(title), title, item["bundle"], None, True)
        forced = item.get("forced") and "over the cap"
        state = "paused" if item.get("paused") else forced or "queued"
        lines.append(
            [
                state,
                get_mark_and_kind(row)[1],
                item["bundle"],
                str(item["priority"]),
                item.get("queued") or "",
                item.get("waits_for") or "",
                item.get("message") or "",
                item.get("answer") or "",
            ],
        )
    return head, lines


def get_move(answer, bundles, at, step):
    """The bundle of the task shown at, and the place of the queue, from 1, that puts it past the
    task shown one step away, which the search may keep far from it; None at an end."""
    if not 0 <= at + step < len(bundles):
        return None
    queue = [item["bundle"] for item in answer["queue"]]
    return bundles[at], queue.index(bundles[at + step]) + 1


def get_default_time(after, now):
    """The time a task is scheduled at by default: its own, else the next 5 minutes after half an
    hour from now."""
    if after:
        return after[-5:]
    start = now + 1800 + 300
    return time.strftime("%H:%M", time.localtime(start - start % 300))


def fill(model, lines):
    """Write the lines over the rows of model, so that the view keeps its scroll, and redraws only
    the rows that changed."""
    for at, line in enumerate(lines):
        if at >= len(model):
            model.append(line)
        elif list(model[at]) != line:
            model[at] = line
    while len(model) > len(lines):
        model.remove(model.get_iter(len(lines)))


def get_child_line(child, place=0):
    """The columns of a row another window adds: no window, no usage, and its age to sort by
    Since."""
    columns = [child.state, child.kind, child.name, child.since, "", "", "", "", child.answer]
    keys = [child.key, "", child.name, place, len(picker.KINDS), child.age]
    return [*columns, *keys, -1.0, -1.0, -1.0]


def get_tree(lines, children, groups):
    """The lines of the agents, each with the lines of the children another window gives it, then
    the groups of that window, each with its children, after every agent: (line, [(line, [])])
    pairs."""
    tree = [(line, get_leaves(children.get(line[KEY], []))) for line in lines]
    for place, (group, kids) in enumerate(groups, len(lines)):
        tree.append((get_child_line(group, place), get_leaves(kids)))
    return tree


def get_leaves(children):
    return [(get_child_line(child), []) for child in children]


def keep_groups(keys, children, groups, shown, search=""):
    """The groups whose name has the search, none when shown is False, and how many rows of the
    owner the agents of keys and the groups with the search have: the number on its button."""
    groups = [(group, kids) for group, kids in groups if is_found(group.name, search)]
    count = sum(len(children.get(key, [])) for key in keys)
    count += sum(len(kids) for _group, kids in groups)
    return groups if shown else [], count


def fill_tree(model, tree, parent=None):
    """fill for a tree: the lines of tree written over the rows under parent, then the lines under
    each over the rows under it."""
    for at, (line, children) in enumerate(tree):
        found = model.iter_nth_child(parent, at)
        if found is None:
            found = model.append(parent, line)
        elif list(model[found]) != line:
            model[found] = line
        fill_tree(model, children, found)
    while model.iter_n_children(parent) > len(tree):
        model.remove(model.iter_nth_child(parent, len(tree)))


def get_all(rows):
    """Each row of a tree, the rows under each one after it."""
    for row in rows:
        yield row
        yield from get_all(row.iterchildren())


def open_hok(args=()):
    """Start the window of hok, which shows the agents with the requests of each: whether it
    runs, or brought up the one open. False when host-ctl is not on this machine, or when hok ends
    with an error within START seconds."""
    if not HOK.is_file():
        return False
    command = [sys.executable, str(HOK), *args]
    started = subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL)
    try:
        return started.wait(START) == 0
    except subprocess.TimeoutExpired:
        return True


def follow(seconds, read, show):
    """Run read in a thread now and every few seconds, one at a time, and show its result in the
    main loop. The function returned runs it once more, unless it runs."""
    running = threading.Lock()

    def work():
        try:
            result = read()
        finally:
            running.release()
        GLib.idle_add(show, result)

    def tick():
        if running.acquire(blocking=False):
            threading.Thread(target=work, daemon=True).start()
        return GLib.SOURCE_CONTINUE

    tick()
    GLib.timeout_add_seconds(seconds, tick)
    return tick


def make_view(model, titles, expand, sorts=()):
    """A view of model with a column per title, each sorted on a click by the model column of the
    same place in sorts, when given. The columns from expand on share the width left, and their
    text is cut to it. The others are cut at WIDTH characters, so a long name keeps the columns
    after it on screen, and are as wide as their text up to there: an ellipsized cell with no
    width_chars asks for 3 characters, and the tree view gives a column what its cells ask for."""
    view = Gtk.TreeView(model=model)
    for at, title in enumerate(titles):
        cell = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        if at < expand:
            cell.props.width_chars = cell.props.max_width_chars = WIDTH
        column = Gtk.TreeViewColumn(title, cell, text=at)
        column.set_expand(at >= expand)
        column.set_resizable(True)
        if sorts:
            column.set_sort_column_id(sorts[at])
        view.append_column(column)
    scrolled = Gtk.ScrolledWindow()
    scrolled.add(view)
    return view, scrolled


def make_flow(widgets):
    """A line of widgets that goes on two lines when its side is too narrow for it: the window
    gets narrower without a scrollbar, and its minimum height grows by one line only."""
    flow = Gtk.FlowBox(
        selection_mode=Gtk.SelectionMode.NONE,
        halign=Gtk.Align.START,
        min_children_per_line=-(-len(widgets) // 2),
        max_children_per_line=len(widgets),
        column_spacing=6,
        row_spacing=6,
    )
    css = Gtk.CssProvider()
    css.load_from_data(b"flowboxchild { padding: 0; }")
    for widget in widgets:
        flow.add(widget)
        widget.get_parent().get_style_context().add_provider(
            css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    return flow


def make_menu(actions):
    menu = Gtk.Menu()
    for label, action in actions:
        item = Gtk.MenuItem(label=label)
        item.connect("activate", action)
        menu.append(item)
    menu.show_all()
    return menu


def follow_color_scheme():
    """Dark when the desktop asks for it, as VS Code and the browsers are, then at each change of
    the color-scheme of the portal. Dark is the dark Adwaita, which GTK always has: the theme of
    the desktop may have no dark variant, or not be installed. The proxy returned is kept, else
    no change comes."""
    settings = Gtk.Settings.get_default()

    def apply(scheme):
        if scheme == 1:
            settings.props.gtk_theme_name = "Adwaita"
            settings.props.gtk_application_prefer_dark_theme = True
        else:
            settings.reset_property("gtk-theme-name")
            settings.reset_property("gtk-application-prefer-dark-theme")

    def changed(_proxy, _sender, signal, parameters):
        if signal == "SettingChanged" and parameters.unpack()[:2] == APPEARANCE:
            apply(parameters.unpack()[2])

    try:
        portal = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
            None,
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings",
            None,
        )
        answer = portal.call_sync(
            "Read", GLib.Variant("(ss)", APPEARANCE), Gio.DBusCallFlags.NONE, 5000, None
        )
    except GLib.Error:
        return None
    apply(answer.unpack()[0])
    portal.connect("g-signal", changed)
    return portal


class Alone:
    """What the window of the agent list adds to the agents: no rows, and the title."""

    def __init__(self, window):
        self.window = window

    def get_children(self, _agents):
        return {}, []

    def select_child(self, _key):
        pass

    def set_title(self, title):
        if self.window.get_title() != title:
            self.window.set_title(title)


class Agents:
    """The filters, the rows, the queue under them and the buttons, in window. Its owner, Alone
    by default, gives the rows under each agent from its key and its session, then its groups
    (get_children), hears which row of its own is selected (select_child), and sets the title."""

    def __init__(self, window, owner=None):
        self.agents_read = []
        self.children = set()
        self.disks = {}
        self.owner = owner or Alone(window)
        self.queue_answer = None
        self.target = None
        self.updating = False
        self.usage = {}
        self.usage_reader = Usage()
        self.window = window

    def build(self, split=None):
        """The widgets, in one box, the queue under its handle at split when given."""
        labels = get_filters([], set(), set())
        self.marks = [Gtk.ToggleButton(label=label) for label in labels[: len(picker.MARKS)]]
        self.kinds = [Gtk.ToggleButton(label=label) for label in labels[len(picker.MARKS) :]]
        self.owned = Gtk.ToggleButton(label=OWNED)
        for button in (self.owned, *self.marks, *self.kinds):
            button.connect("toggled", self.show)
        self.owned.connect("toggled", lambda _button: self.agents_view.expand_all())
        self.search = Gtk.SearchEntry(placeholder_text="Branch")
        self.search.connect("search-changed", self.search_changed)
        filters = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        owned = [] if isinstance(self.owner, Alone) else [self.owned]
        filters.pack_start(make_flow([*owned, *self.marks]), False, False, 0)
        line = make_flow([make_flow(self.kinds), self.search])
        line.set_halign(Gtk.Align.FILL)
        filters.pack_start(line, False, False, 0)
        self.agents = Gtk.TreeStore(*[str] * 12, int, int, float, float, float, float)
        self.sorted_agents = Gtk.TreeModelSort(model=self.agents)
        self.agents_view, agents = make_view(self.sorted_agents, COLUMNS, 8, SORTS)
        self.agents_view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.agents_view.get_selection().connect("changed", self.selected)
        self.agents_view.connect("row-activated", self.bring_up)
        self.queue_head = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.queue = Gtk.ListStore(*[str] * len(QUEUE_COLUMNS))
        self.queue_view, queue = make_view(self.queue, QUEUE_COLUMNS, 6)
        self.queue_view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        queue.set_min_content_height(100)
        queue_actions = (
            ("Force", self.force),
            ("Up", lambda _button: self.move(-1)),
            ("Down", lambda _button: self.move(1)),
            ("Pause", lambda _button: self.schedule("manual")),
            ("Resume", lambda _button: self.schedule("spot")),
            ("Time", self.schedule_at),
        )
        self.queue_view.connect("button-press-event", self.open_menu, make_menu(queue_actions))
        queue_buttons = Gtk.Box(spacing=6)
        for label, action in queue_actions:
            button = Gtk.Button(label=label)
            button.connect("clicked", action)
            queue_buttons.pack_start(button, False, False, 0)
        queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        queue_box.pack_start(self.queue_head, False, False, 0)
        queue_box.pack_start(queue, True, True, 0)
        queue_box.pack_start(queue_buttons, False, False, 0)
        self.panes = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, wide_handle=True)
        self.panes.pack1(agents, True, False)
        self.panes.pack2(queue_box, False, False)
        if split is not None:
            self.panes.set_position(split)
        actions = (
            ("Terminal", self.bring_up),
            ("VS Code", self.open_code),
            ("Show tabs", lambda *_args: self.ask_tabs("show", first=True)),
            ("Close tabs", lambda *_args: self.ask_tabs("close")),
            ("Close", self.close),
            ("Back to queue", self.queue_again),
            ("Pause", lambda *_args: self.queue_again(paused=True)),
        )
        self.agents_view.connect("button-press-event", self.open_menu, make_menu(actions))
        buttons = []
        for label, action in (*actions, ("New ops agent", lambda _button: picker.open_ops())):
            button = Gtk.Button(label=label)
            button.connect("clicked", action)
            buttons.append(button)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
        box.pack_start(filters, False, False, 0)
        box.pack_start(self.panes, True, True, 0)
        box.pack_start(make_flow(buttons), False, False, 0)
        return box

    def start(self):
        """Read the agents, the queue, the usage and the disks, each in a thread, from now on."""
        follow(AGENTS_CHECK, read_agents, self.show_agents)
        self.read_queue = follow(QUEUE_CHECK, read_queue, self.show_queue)
        follow(USAGE_CHECK, self.usage_reader.read, self.show_usage)
        self.measure_disks = follow(DISK_CHECK, self.usage_reader.read_disks, self.show_disks)

    def get_selected(self, every=False):
        """The selected agents, or with every all the selected rows, those of the owner too."""
        model, paths = self.agents_view.get_selection().get_selected_rows()
        rows = [list(model[path]) for path in paths]
        return rows if every else [row for row in rows if row[WINDOW]]

    def selected(self, _selection):
        if not self.updating:
            found = (row[KEY] for row in self.get_selected(every=True) if not row[WINDOW])
            self.owner.select_child(next(found, None))

    def select(self, key):
        """Select the row of key alone, open the rows above it and scroll to it: whether there
        is one."""
        row = next((row for row in get_all(self.sorted_agents) if row[KEY] == key), None)
        if row is None:
            return False
        self.agents_view.expand_to_path(row.path)
        selection = self.agents_view.get_selection()
        selection.unselect_all()
        selection.select_path(row.path)
        self.agents_view.scroll_to_cell(row.path, None, False, 0, 0)
        return True

    def pick(self, key):
        """Select the row of key once the rows show it, for PICK_TIME seconds at most. An ops agent
        window is found by its session (ops-SESSION)."""
        self.target = (key, time.monotonic() + PICK_TIME)
        self.select_target()

    def select_target(self):
        if self.target is None:
            return
        key, until = self.target
        if key.startswith(OPS):
            sessions = {info[-1]: row.key for row, info in self.agents_read if info[-1]}
            key = sessions.get(key.removeprefix(OPS), key)
        if self.select(key) or time.monotonic() > until:
            self.target = None

    def search_changed(self, _entry):
        self.show()
        self.show_queue(self.queue_answer)

    def show(self, *_args):
        """Write the rows the filters keep, with what was read last and the rows of the owner
        under them, and keep their selection. A row of the owner that is new opens the rows."""
        ranks = {rank for rank, button in enumerate(self.marks) if button.get_active()}
        kinds = {kind for kind, button in zip(picker.KINDS, self.kinds) if button.get_active()}
        search = self.search.get_text()
        rows = [row for row, _info in self.agents_read]
        agents = [(row.key, info[-1]) for row, info in self.agents_read]
        children, groups = self.owner.get_children(agents)
        owned = set(children) if self.owned.get_active() else None
        labels = get_filters(rows, ranks, kinds, search, owned)
        for button, label in zip(self.marks + self.kinds, labels):
            if button.get_label() != label:
                button.set_label(label)
        lines = [
            self.get_line(place, row, info)
            for place, (row, info) in enumerate(self.agents_read)
            if is_kept(row, ranks, kinds, search, owned)
        ]
        counted = [row.key for row in rows if is_kept(row, set(), kinds, search)]
        shown = owned is not None or not ranks
        groups, count = keep_groups(counted, children, groups, shown, search)
        if self.owned.get_label() != f"{OWNED} {count}":
            self.owned.set_label(f"{OWNED} {count}")
        tree = get_tree(lines, children, groups)
        selection = self.agents_view.get_selection()
        keys = {row[KEY] for row in self.get_selected(every=True)}
        self.updating = True
        fill_tree(self.agents, tree)
        selection.unselect_all()
        for row in get_all(self.sorted_agents):
            if row[KEY] in keys:
                selection.select_iter(row.iter)
        self.updating = False
        children = {kid[KEY] for _line, kids in tree for kid, _none in kids}
        if children - self.children:
            self.agents_view.expand_all()
        if bool(children) != self.agents_view.get_show_expanders():
            self.agents_view.set_show_expanders(bool(children))
        self.children = children
        self.select_target()

    def get_line(self, place, row, info):
        """The columns of a row, then its key, window and title, then the values its columns sort
        by: its place in the picker for its mark, the order of its kind, the time of its state, its
        CPU, RAM and disk, -1 when not read."""
        state, answer, since, tabs, _session = info
        cpu, ram = self.usage.get(row.key, (None, None))
        disk = self.disks.get(row.key)
        return [
            *get_mark_and_kind(row),
            get_name(row),
            state,
            "" if cpu is None else f"{cpu:.0f}%",
            "" if ram is None else format_size(ram),
            "" if disk is None else format_size(disk),
            tabs,
            answer,
            row.key,
            row.window,
            row.title,
            place,
            picker.get_order(row.title),
            since,
            -1.0 if cpu is None else cpu,
            -1.0 if ram is None else float(ram),
            -1.0 if disk is None else float(disk),
        ]

    def show_agents(self, agents):
        keys = [(row.key, row.window) for row, _info in agents]
        new = set(keys) - set(self.usage_reader.keys)
        self.usage_reader.keys = keys
        self.agents_read = agents
        self.show()
        self.owner.set_title(get_window_title([row for row, _info in agents]))
        if new:
            self.measure_disks()

    def show_usage(self, usage):
        self.usage = usage
        self.show()

    def show_disks(self, disks):
        self.disks = disks
        self.show()

    def show_queue(self, answer):
        self.queue_answer = answer
        rows = [row for row, _info in self.agents_read]
        head, lines = describe_queue(answer, rows, self.search.get_text())
        self.queue_head.set_text(head)
        selection = self.queue_view.get_selection()
        bundles = set(self.get_queued())
        fill(self.queue, lines)
        selection.unselect_all()
        for row in self.queue:
            if row[BUNDLE] in bundles:
                selection.select_iter(row.iter)

    def get_queued(self):
        model, paths = self.queue_view.get_selection().get_selected_rows()
        return [model[path][BUNDLE] for path in paths]

    def send(self, path, body):
        """Ask the daemon, say its error if it gives one, and read the queue again."""
        answer = ask("POST", path, body)
        if "error" in answer:
            self.say(answer["error"])
        self.read_queue()
        return "error" not in answer

    def say(self, text):
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=text,
        )
        dialog.run()
        dialog.destroy()

    def force(self, _button):
        for bundle in self.get_queued():
            self.send("/force-queue", {"bundle": bundle})

    def move(self, step):
        """Move the first selected task past the one shown above or below it."""
        model, paths = self.queue_view.get_selection().get_selected_rows()
        bundles = [row[BUNDLE] for row in model]
        at = paths and paths[0].get_indices()[0]
        if paths and (found := get_move(self.queue_answer, bundles, at, step)):
            self.send("/move", {"bundle": found[0], "place": found[1]})

    def schedule(self, when):
        for bundle in self.get_queued():
            self.send("/schedule", {"bundle": bundle, "when": when})

    def schedule_at(self, _button):
        """Ask a time of the day, the one of the first selected task by default, and schedule
        each selected task at it."""
        bundles = self.get_queued()
        if not bundles:
            return
        queue = (self.queue_answer or {}).get("queue", [])
        after = next((item.get("after") for item in queue if item["bundle"] == bundles[0]), None)
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            text="Start at what time? HH:MM, today or tomorrow.",
            secondary_text="\n".join(bundles),
        )
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Schedule", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry(text=get_default_time(after, time.time()), activates_default=True)
        dialog.get_message_area().pack_start(entry, False, False, 0)
        entry.show()
        answer = dialog.run()
        when = entry.get_text().strip()
        dialog.destroy()
        if answer == Gtk.ResponseType.OK:
            self.schedule(when)

    def open_menu(self, view, event, menu):
        """On a right click, select the row under the mouse unless it is selected, and open menu,
        that of the selected rows."""
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != Gdk.BUTTON_SECONDARY:
            return False
        found = view.get_path_at_pos(int(event.x), int(event.y))
        if found is None:
            return False
        selection = view.get_selection()
        if not selection.path_is_selected(found[0]):
            selection.unselect_all()
            selection.select_path(found[0])
        menu.popup_at_pointer(event)
        return True

    def bring_up(self, *_args):
        for row in reversed(self.get_selected()):
            subprocess.run(["wmctrl", "-i", "-a", row[WINDOW]], check=False)

    def open_code(self, *_args):
        for row in self.get_selected():
            if row[KEY] != row[WINDOW]:
                picker.pick(row[KEY], terminal=False)

    def ask_tabs(self, op, first=False):
        """Bring up the Firefox tab group of the first selected bundle, or close the groups of
        each, at once: a closed tab comes back with Ctrl+Shift+T."""
        rows = [row for row in self.get_selected() if row[KEY] != row[WINDOW]]
        for row in rows[:1] if first else rows:
            answer = tab_groups.ask({"op": op, "bundle": row[KEY]})
            if answer and "error" in answer:
                self.say(answer["error"])

    def confirm(self, text, rows, action):
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            text=text,
            secondary_text="\n".join(row[TITLE] for row in rows),
        )
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, action, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        answer = dialog.run()
        dialog.destroy()
        return answer == Gtk.ResponseType.OK

    def close(self, *_args):
        rows = self.get_selected()
        text = "Close the window of these agents? Their claude stops."
        if rows and self.confirm(text, rows, "Close"):
            close_windows([row[WINDOW] for row in rows])

    def queue_again(self, *_args, paused=False):
        """Queue the session of each selected bundle again, last: the daemon closes its window
        once its turn ended, and the queue starts it again when its turn comes, or once it is
        resumed when paused."""
        rows = [row for row in self.get_selected() if row[KEY] != row[WINDOW]]
        text = "Each one ends its turn, 2 minutes at most, then its window closes"
        if paused:
            text = f"Pause these agents? {text}, and it waits at the end of the queue until you"
            text += " resume it."
        else:
            text = f"Put these agents back at the end of the queue? {text}."
        if not rows or not self.confirm(text, rows, "Pause" if paused else "Back to queue"):
            return
        for row in rows:
            self.send("/requeue", {"bundle": row[KEY], "paused": paused})


class AgentList(Gtk.Application):
    def __init__(self):
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        super().__init__(application_id="org.bundle_ctl.AgentList", flags=flags)
        self.agents = self.window = None

    def do_command_line(self, line):
        args = line.get_arguments()[1:]
        self.activate()
        if args[:1] == ["--select"] and args[1:]:
            self.agents.pick(args[1])
        return 0

    def do_activate(self):
        if self.window:
            xid = self.window.get_window().get_xid()
            subprocess.run(["wmctrl", "-i", "-a", hex(xid)], check=False)
            return
        self.portal = follow_color_scheme()
        with contextlib.suppress(GLib.Error):
            Gtk.Window.set_default_icon_from_file(str(ICON))
        self.window = Gtk.ApplicationWindow(application=self, title=get_window_title([]))
        self.window.connect("delete-event", self.save_place)
        try:
            place = json.loads(PLACE.read_text())
            self.window.set_default_size(place["width"], place["height"])
            self.window.move(place["x"], place["y"])
        except (OSError, ValueError, KeyError):
            place = {}
            self.window.set_default_size(900, 500)
        self.agents = Agents(self.window)
        self.window.add(self.agents.build(place.get("split")))
        self.window.show_all()
        self.agents.start()

    def save_place(self, window, _event):
        x, y = window.get_position()
        width, height = window.get_size()
        split = self.agents.panes.get_position()
        place = {"height": height, "split": split, "width": width, "x": x, "y": y}
        PLACE.parent.mkdir(parents=True, exist_ok=True)
        PLACE.write_text(json.dumps(place))


if __name__ == "__main__":
    if not open_hok(sys.argv[1:]):
        AgentList().run(sys.argv)
