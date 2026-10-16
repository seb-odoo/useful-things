#!/usr/bin/python3
"""A window that stays open on the rows of window-picker.py and on the queue of the agents, read
again every 2 seconds, the queue every 10.

    agent-list.py

The rows are the ones of the picker, the terminals shown on screen included: each with its mark, its
kind, its bundle or the title of its conversation, the state of its agent and since when, ", shown"
when its terminal is not minimized, its CPU, RAM and disk, and the close line of the last answer of
its session (picker.get_close_lines), else the first line of the result.md of a bundle. A click on a column header sorts the rows by that column: by mark in the order of the picker, by
kind in its order, by time, by number, or by text. CPU and RAM are those of the container of a
bundle, its file cache aside, or of the claude of an ops window and its children, as the ops agents share
one container; read every 5 seconds, 100% is one core. Disk is the folder of a bundle, measured
with du when its row comes, then every 10 minutes.

The buttons above the rows count the rows of each mark among the kinds pressed, and of each kind
among the marks pressed. Pressed, they keep the rows of one of the marks pressed and of one of the
kinds pressed. Enter, a double click or Terminal brings up the terminal of each selected row
(Ctrl+click, Shift+click) where it was, the first row last, so that it ends in front. VS Code opens
the window of each selected bundle, which takes its session over. Close closes the window of each
selected row after a question, as the daemon does: SIGUSR1 to its agent-terminator.py, and its
claude stops. Back to queue queues the session of each selected bundle again, at the end of the
queue (the lowest priority there), then closes its window: the queue starts it again with "Continue
from where you left off." when its turn comes. Pause does the same, and the task waits in the
queue for a manual resume. A right click on a row opens a menu with these five. New ops agent runs
bctl open ops --terminal. Under the queue, Force lets each selected task start over the cap, Up and
Down move the first selected task one place, Pause makes each selected task wait for a manual
resume, Resume for a slot, and Time for a time of the day it asks (bctl schedule). A second start
brings the open
window up. The size and the place of the window, and the height of the queue under its handle, are
kept in agent-list.json, beside the state of the daemon.
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
from gi.repository import Gdk, GdkX11, GLib, Gtk, Pango  # noqa: F401

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from agents import OPS, get_agent_folder, get_mtime, read_text  # noqa: E402
from commands import get_worktree_bundle_folder  # noqa: E402

AGENTS_CHECK = 2
COLUMNS = ("State", "Type", "Agent", "Since", "CPU", "RAM", "Disk", "Last answer")
CONTAINERS = pathlib.Path(
    f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/user.slice"
)
DISK_AGE = 600
DISK_CHECK = 60
KEY, WINDOW, TITLE = 8, 9, 10
SORTS = (11, 12, 2, 13, 14, 15, 16, 7)
PLACE = (
    pathlib.Path(os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local/state")
    / "bundle-ctl"
    / "agent-list.json"
)
QUEUE_CHECK = 10
SCOPE = re.compile(r"libpod-([0-9a-f]{64})\.scope")
USAGE_CHECK = 5


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bctl = load("bctl", HERE / "client" / "bctl.py")
picker = load("picker", HERE / "window-picker.py")
runs = load("runs", HERE / "runs.py")
ICONS = {kind: icon for icon, kind, _ in load("title", HERE / "client" / "window-title.py").KINDS}


def get_info(row, close):
    """The state of the agent of a row and since when, from the .agent/ files of its bundle, the
    close line of its last answer, else the first line of its result.md, and that since as a time,
    0 when none. An ops row has its window as key, and no such files."""
    shown = [] if row.minimized else ["shown"]
    if row.key == row.window:
        return ", ".join(shown), close, 0.0
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
    return ", ".join([state, *shown]), answer, since or 0.0


def read_agents():
    rows = picker.get_rows(picker.get_windows(), shown=True)
    closes = picker.get_close_lines(rows)
    return [(row, get_info(row, closes[row.key])) for row in rows]


def get_kind(row):
    order = picker.get_order(row.title)
    return picker.KINDS[order] if order < len(picker.KINDS) else None


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


def get_filters(rows, ranks, kinds):
    """The label of each filter button: each mark, then each kind, with its number of rows among
    the ones the pressed buttons of the other line keep."""
    found = [get_kind(row) for row in rows if is_kept(row, ranks, set())]
    labels = [f"{ICONS[kind]} {kind} {found.count(kind)}" for kind in picker.KINDS]
    return [*picker.get_counts([row for row in rows if is_kept(row, set(), kinds)]), *labels]


def is_kept(row, ranks, kinds):
    """Whether a row has one of the marks of ranks and one of the kinds, an empty set keeps all."""
    return (not ranks or row.rank in ranks) and (not kinds or get_kind(row) in kinds)


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


def read_queue():
    """The answer of GET /queue, None when the daemon does not answer."""
    answer = ask("GET", "/queue")
    return None if "error" in answer else answer


def describe_queue(answer):
    if answer is None:
        return "bundle-ctl does not answer", []
    head = f"{answer['agents']}/{answer['max_agents']} agents at work in a terminal"
    head += f", {len(answer['queue'])} queued"
    if answer["memory"]["short"]:
        head += f"; {bctl.format_memory(answer['memory'])}"
    lines = [
        [
            item["bundle"],
            str(item["priority"]),
            item.get("queued") or "",
            ", ".join(filter(None, [item.get("waits_for"), item.get("forced") and "over the cap"])),
        ]
        for item in answer["queue"]
    ]
    return head, lines


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
    same place in sorts, when given."""
    view = Gtk.TreeView(model=model)
    for at, title in enumerate(titles):
        cell = Gtk.CellRendererText()
        if at == expand:
            cell.props.ellipsize = Pango.EllipsizeMode.END
        column = Gtk.TreeViewColumn(title, cell, text=at)
        column.set_expand(at == expand)
        column.set_resizable(True)
        if sorts:
            column.set_sort_column_id(sorts[at])
        view.append_column(column)
    scrolled = Gtk.ScrolledWindow()
    scrolled.add(view)
    return view, scrolled


class AgentList(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.bundle_ctl.AgentList")
        self.agents_read = []
        self.disks = {}
        self.queue_answer = None
        self.usage = {}
        self.usage_reader = Usage()
        self.window = None

    def do_activate(self):
        if self.window:
            xid = self.window.get_window().get_xid()
            subprocess.run(["wmctrl", "-i", "-a", hex(xid)], check=False)
            return
        self.window = Gtk.ApplicationWindow(application=self, title="Agent list")
        self.window.connect("delete-event", self.save_place)
        try:
            place = json.loads(PLACE.read_text())
            self.window.set_default_size(place["width"], place["height"])
            self.window.move(place["x"], place["y"])
        except (OSError, ValueError, KeyError):
            place = {}
            self.window.set_default_size(900, 500)
        labels = get_filters([], set(), set())
        self.marks = [Gtk.ToggleButton(label=label) for label in labels[: len(picker.MARKS)]]
        self.kinds = [Gtk.ToggleButton(label=label) for label in labels[len(picker.MARKS) :]]
        filters = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for buttons in (self.marks, self.kinds):
            line = Gtk.Box(spacing=6)
            for button in buttons:
                button.connect("toggled", self.show)
                line.pack_start(button, False, False, 0)
            filters.pack_start(line, False, False, 0)
        self.agents = Gtk.ListStore(*[str] * 11, int, int, float, float, float, float)
        self.sorted_agents = Gtk.TreeModelSort(model=self.agents)
        self.agents_view, agents = make_view(self.sorted_agents, COLUMNS, 7, SORTS)
        self.agents_view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.agents_view.connect("button-press-event", self.open_menu)
        self.agents_view.connect("row-activated", self.bring_up)
        self.queue_head = Gtk.Label(xalign=0)
        self.queue = Gtk.ListStore(str, str, str, str)
        titles = ("Queued", "Priority", "Since", "Waits for")
        self.queue_view, queue = make_view(self.queue, titles, 3)
        self.queue_view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        queue.set_min_content_height(100)
        queue_buttons = Gtk.Box(spacing=6)
        for label, action in (
            ("Force", self.force),
            ("Up", lambda _button: self.move(-1)),
            ("Down", lambda _button: self.move(1)),
            ("Pause", lambda _button: self.schedule("manual")),
            ("Resume", lambda _button: self.schedule("spot")),
            ("Time", self.schedule_at),
        ):
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
        if "split" in place:
            self.panes.set_position(place["split"])
        actions = (
            ("Terminal", self.bring_up),
            ("VS Code", self.open_code),
            ("Close", self.close),
            ("Back to queue", self.queue_again),
            ("Pause", lambda *_args: self.queue_again(paused=True)),
        )
        self.menu = Gtk.Menu()
        for label, action in actions:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", action)
            self.menu.append(item)
        self.menu.show_all()
        buttons = Gtk.Box(spacing=6)
        for label, action in (*actions, ("New ops agent", lambda _button: picker.open_ops())):
            button = Gtk.Button(label=label)
            button.connect("clicked", action)
            buttons.pack_start(button, False, False, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
        box.pack_start(filters, False, False, 0)
        box.pack_start(self.panes, True, True, 0)
        box.pack_start(buttons, False, False, 0)
        self.window.add(box)
        self.window.show_all()
        follow(AGENTS_CHECK, read_agents, self.show_agents)
        self.read_queue = follow(QUEUE_CHECK, read_queue, self.show_queue)
        follow(USAGE_CHECK, self.usage_reader.read, self.show_usage)
        self.measure_disks = follow(DISK_CHECK, self.usage_reader.read_disks, self.show_disks)

    def get_selected(self):
        model, paths = self.agents_view.get_selection().get_selected_rows()
        return [list(model[path]) for path in paths]

    def show(self, *_args):
        """Write the rows the filters keep, with what was read last, and keep their selection."""
        ranks = {rank for rank, button in enumerate(self.marks) if button.get_active()}
        kinds = {kind for kind, button in zip(picker.KINDS, self.kinds) if button.get_active()}
        labels = get_filters([row for row, _info in self.agents_read], ranks, kinds)
        for button, label in zip(self.marks + self.kinds, labels):
            if button.get_label() != label:
                button.set_label(label)
        lines = [
            self.get_line(place, row, info)
            for place, (row, info) in enumerate(self.agents_read)
            if is_kept(row, ranks, kinds)
        ]
        selection = self.agents_view.get_selection()
        keys = {row[KEY] for row in self.get_selected()}
        fill(self.agents, lines)
        selection.unselect_all()
        for row in self.sorted_agents:
            if row[KEY] in keys:
                selection.select_iter(row.iter)

    def get_line(self, place, row, info):
        """The columns of a row, then its key, window and title, then the values its columns sort
        by: its place in the picker for its mark, the order of its kind, the time of its state, its
        CPU, RAM and disk, -1 when not read."""
        state, answer, since = info
        cpu, ram = self.usage.get(row.key, (None, None))
        disk = self.disks.get(row.key)
        kind = get_kind(row)
        return [
            " ".join(picker.MARKS[row.rank]) if row.rank < len(picker.MARKS) else "",
            f"{ICONS[kind]} {kind}" if kind else "",
            get_name(row),
            state,
            "" if cpu is None else f"{cpu:.0f}%",
            "" if ram is None else format_size(ram),
            "" if disk is None else format_size(disk),
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
        head, lines = describe_queue(answer)
        self.queue_head.set_text(head)
        selection = self.queue_view.get_selection()
        bundles = set(self.get_queued())
        fill(self.queue, lines)
        selection.unselect_all()
        for row in self.queue:
            if row[0] in bundles:
                selection.select_iter(row.iter)

    def get_queued(self):
        model, paths = self.queue_view.get_selection().get_selected_rows()
        return [model[path][0] for path in paths]

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
        """Move the first selected task one place up or down the queue."""
        model, paths = self.queue_view.get_selection().get_selected_rows()
        if paths and 1 <= (place := paths[0].get_indices()[0] + 1 + step) <= len(model):
            self.send("/move", {"bundle": model[paths[0]][0], "place": place})

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

    def open_menu(self, view, event):
        """On a right click, select the row under the mouse unless it is selected, and open the
        menu of the selected rows."""
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != Gdk.BUTTON_SECONDARY:
            return False
        found = view.get_path_at_pos(int(event.x), int(event.y))
        if found is None:
            return False
        selection = view.get_selection()
        if not selection.path_is_selected(found[0]):
            selection.unselect_all()
            selection.select_path(found[0])
        self.menu.popup_at_pointer(event)
        return True

    def bring_up(self, *_args):
        for row in reversed(self.get_selected()):
            subprocess.run(["wmctrl", "-i", "-a", row[WINDOW]], check=False)

    def open_code(self, *_args):
        for row in self.get_selected():
            if row[KEY] != row[WINDOW]:
                picker.pick(row[KEY], terminal=False)

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
        """Queue the session of each selected bundle again, last, then close its window: its
        claude stops, and the queue starts it again when its turn comes, or once it is resumed
        when paused."""
        rows = [row for row in self.get_selected() if row[KEY] != row[WINDOW]]
        text = "Their window closes, claude stops"
        if paused:
            text = f"Pause these agents? {text}, and they wait at the end of the queue until you"
            text += " resume them."
        else:
            text = f"Put these agents back at the end of the queue? {text}."
        if not rows or not self.confirm(text, rows, "Pause" if paused else "Back to queue"):
            return
        queue = (self.queue_answer or {}).get("queue", [])
        priority = min((item["priority"] for item in queue), default=0)
        for row in rows:
            body = {"bundle": row[KEY], "priority": priority, "resume": True, "task": bctl.GO_ON}
            if paused:
                body["paused"] = True
            if self.send("/open", body):
                close_windows([row[WINDOW]])

    def save_place(self, window, _event):
        x, y = window.get_position()
        width, height = window.get_size()
        split = self.panes.get_position()
        place = {"height": height, "split": split, "width": width, "x": x, "y": y}
        PLACE.parent.mkdir(parents=True, exist_ok=True)
        PLACE.write_text(json.dumps(place))


if __name__ == "__main__":
    AgentList().run()
