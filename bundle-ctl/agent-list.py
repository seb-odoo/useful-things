#!/usr/bin/python3
"""A window that stays open on the rows of window-picker.py and on the queue of the agents, read
again every 2 seconds, the queue every 10.

    agent-list.py

The rows are the ones of the picker, the terminals shown on screen included: each bundle with the
state of its agent and since when, ", shown" when its terminal is not minimized, and the first line
of its last answer. Enter, a double click or Terminal brings up the terminal of each selected row
(Ctrl+click, Shift+click) where it was, the first row last, so that it ends in front. VS Code opens
the window of each selected bundle, which takes its session over. New ops agent runs bctl open ops
--terminal. A second start brings the open window up. The size and the place of the window are
kept in agent-list.json, beside the state of the daemon.
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

import gi

gi.require_version("GdkX11", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkX11, GLib, Gtk, Pango  # noqa: F401

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from agents import get_agent_folder, get_mtime, read_text  # noqa: E402

AGENTS_CHECK = 2
KEY, WINDOW = 3, 4
PLACE = (
    pathlib.Path(os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local/state")
    / "bundle-ctl"
    / "agent-list.json"
)
QUEUE_CHECK = 10


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bctl = load("bctl", HERE / "client" / "bctl.py")
picker = load("picker", HERE / "window-picker.py")


def get_info(row):
    """The state of the agent of a row and since when, from the .agent/ files of its bundle, then
    the first line of its last answer. An ops row has its window as key, and no such files."""
    shown = [] if row.minimized else ["shown"]
    if row.key == row.window:
        return ", ".join(shown), ""
    folder = get_agent_folder(row.key)
    now = time.time()
    if (done := get_mtime(folder / "done")) is not None:
        word = "failed" if read_text(folder / "done") == "failed" else "done"
        state = f"{word} {bctl.format_span(now - done)} ago"
    elif (waiting := get_mtime(folder / "waiting")) is not None:
        state = f"waiting for {bctl.format_span(now - waiting)}"
    elif (started := get_mtime(folder / "run.lock")) is not None:
        state = f"running for {bctl.format_span(now - started)}"
    else:
        state = "starting"
    answer = (read_text(folder / "result.md") or "").partition("\n")[0]
    return ", ".join([state, *shown]), answer


def read_agents():
    rows = picker.get_rows(picker.get_windows(), shown=True)
    lines = [[row.title, *get_info(row), row.key, row.window] for row in rows]
    return "   ".join(picker.get_counts(rows)), lines


def read_queue():
    """The answer of GET /queue, None when the daemon does not answer."""
    path = next((path for path in bctl.SOCKETS if path.exists()), None)
    if path is None:
        return None
    connection = bctl.UnixConnection(path, QUEUE_CHECK)
    try:
        connection.request("GET", "/queue")
        response = connection.getresponse()
        return json.loads(response.read()) if response.status == 200 else None
    except (OSError, ValueError):
        return None
    finally:
        connection.close()


def describe_queue(answer):
    if answer is None:
        return "bundle-ctl does not answer", []
    head = f"{answer['agents']}/{answer['max_agents']} agents at work in a terminal"
    head += f", {len(answer['queue'])} queued"
    if answer["memory"]["short"]:
        head += f"; {bctl.format_memory(answer['memory'])}"
    lines = [
        [item["bundle"], str(item["priority"]), item.get("queued") or "", item.get("waits_for") or ""]
        for item in answer["queue"]
    ]
    return head, lines


def fill(model, lines):
    """Write the lines over the rows of model, so that the view keeps its scroll."""
    for at, line in enumerate(lines):
        if at < len(model):
            model[at] = line
        else:
            model.append(line)
    while len(model) > len(lines):
        model.remove(model.get_iter(len(lines)))


def follow(seconds, read, show):
    """Run read in a thread now and every few seconds, one at a time, and show its result in the
    main loop."""
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


def make_view(model, titles, expand):
    view = Gtk.TreeView(model=model)
    for at, title in enumerate(titles):
        cell = Gtk.CellRendererText()
        if at == expand:
            cell.props.ellipsize = Pango.EllipsizeMode.END
        column = Gtk.TreeViewColumn(title, cell, text=at)
        column.set_expand(at == expand)
        column.set_resizable(True)
        view.append_column(column)
    scrolled = Gtk.ScrolledWindow()
    scrolled.add(view)
    return view, scrolled


class AgentList(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.bundle_ctl.AgentList")
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
            self.window.set_default_size(900, 500)
        self.counts = Gtk.Label(xalign=0)
        self.agents = Gtk.ListStore(str, str, str, str, str)
        self.agents_view, agents = make_view(self.agents, ("Agent", "State", "Last answer"), 2)
        self.agents_view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.agents_view.connect("row-activated", self.bring_up)
        self.queue_head = Gtk.Label(xalign=0)
        self.queue = Gtk.ListStore(str, str, str, str)
        queue_view, queue = make_view(self.queue, ("Queued", "Priority", "Since", "Waits for"), 3)
        queue_view.get_selection().set_mode(Gtk.SelectionMode.NONE)
        queue.set_min_content_height(100)
        buttons = Gtk.Box(spacing=6)
        for label, action in (
            ("Terminal", self.bring_up),
            ("VS Code", self.open_code),
            ("New ops agent", lambda _button: picker.open_ops()),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", action)
            buttons.pack_start(button, False, False, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=6)
        box.pack_start(self.counts, False, False, 0)
        box.pack_start(agents, True, True, 0)
        box.pack_start(self.queue_head, False, False, 0)
        box.pack_start(queue, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        self.window.add(box)
        self.window.show_all()
        follow(AGENTS_CHECK, read_agents, self.show_agents)
        follow(QUEUE_CHECK, read_queue, self.show_queue)

    def get_selected(self):
        model, paths = self.agents_view.get_selection().get_selected_rows()
        return [(model[path][KEY], model[path][WINDOW]) for path in paths]

    def show_agents(self, result):
        counts, lines = result
        self.counts.set_text(counts)
        selection = self.agents_view.get_selection()
        keys = {key for key, _ in self.get_selected()}
        fill(self.agents, lines)
        selection.unselect_all()
        for row in self.agents:
            if row[KEY] in keys:
                selection.select_iter(row.iter)

    def show_queue(self, answer):
        head, lines = describe_queue(answer)
        self.queue_head.set_text(head)
        fill(self.queue, lines)

    def bring_up(self, *_args):
        for _key, window in reversed(self.get_selected()):
            subprocess.run(["wmctrl", "-i", "-a", window], check=False)

    def open_code(self, _button):
        for key, window in self.get_selected():
            if key != window:
                picker.pick(key, terminal=False)

    def save_place(self, window, _event):
        x, y = window.get_position()
        width, height = window.get_size()
        PLACE.parent.mkdir(parents=True, exist_ok=True)
        PLACE.write_text(json.dumps({"height": height, "width": width, "x": x, "y": y}))


if __name__ == "__main__":
    AgentList().run()
