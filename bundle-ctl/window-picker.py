#!/usr/bin/env python3
"""List the bundles not open in VS Code, the ops agent windows and the other windows for rofi, the
bundles and the ops windows first by the mark of their title, then by kind, and bring the one
picked to the front. A ❌ row comes first, as an error to fix, then a ✅ or ✋ row of an ops, fw,
runbot or mine agent, by kind then by mark, as it waits for Seb (Seb, 2026-10-15): a ✋ fw comes
after a ✅ fw, before a ✅ runbot.

    window-picker.py --show
    window-picker.py --counts
    window-picker.py --agent BUNDLE
    window-picker.py --terminal BUNDLE
    window-picker.py --code BUNDLE

--show opens rofi on this script, where the row under the mouse is selected. A click on a bundle
brings its VS Code window up, or opens it through its `odoo-bundle://` link; a right click
or Shift+Enter brings its terminal up, when it has one. A click on another window brings it up. A
bundle whose VS Code window is opening shows 🔄 for up to 5 minutes, until that window takes its
row away: closed later, the window gives the row back without 🔄.

A rofi script: it prints the rows when called with no argument, and rofi calls it again on a pick,
with the bundle or the window id in ROFI_INFO. A bundle has one row, from the title of its terminal
(client/window-title.py), and none once its VS Code window is open: Seb then works there. Nor
has a bundle or an ops agent whose terminal is not minimized: it is in the task bar. After the
title, a row shows the close line of the last answer of its session, `Nothing left` or `Waiting
for you` (Seb, 2026-10-16), which rofi cuts to the width of its window. The terminal
keeps its last mark once its claude ends, so a ✅, ✋ or ⏳ shows only while that claude runs. A
claude that ends on an error leaves its window on that error until Enter: its row then shows ❌
in place of its mark (Seb, 2026-10-19), and the agent list that error. Each
mark gets a word that the search matches but the row does not show, as the emoji are hard to type;
the kind is already in words. The line above the rows counts the bundles of each mark. --counts
prints the mark that needs Seb most with its count (PRIORITY), or 🪟 when no bundle has a mark,
then that line, for window-applet/.

A minimized ops agent window has a row of its own, ranked by its mark, and a click brings it up:
its title shows the title of its conversation in place of a name, and agent-terminator.py drops its
mark once its claude ended. The first row, `➕ new ops agent`, opens a new one: bctl open ops
--terminal.

--agent brings up the terminal of the agent of a bundle, else its VS Code window, which then holds
its session, else starts that agent again in a terminal on its last session (bctl open
--terminal), and brings the window up once it shows, as it opens minimized. For an ops session,
--agent ops-SESSION brings up its ops agent window, else the ops window when a tab of it runs the
session, else goes on with the session in a new agent window: hok links the caller of a request
there. --terminal is --agent without the VS Code window, which bctl then takes the session from:
ocode -t. --code brings up its
VS Code window, else opens it through its `odoo-bundle://` link. The helper of firefox-gateway runs
them from a tab of the Firefox group of the bundle.
"""

import collections
import ctypes
import functools
import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from agents import OPS, get_agent_folder, read_text  # noqa: E402
from config import CLAUDE_CONFIG_CONTAINER, HOST_FOLDER  # noqa: E402

from cut import get_live_sessions  # noqa: E402

AGENT_WAIT = 60
FAILED = "❌"
FIRST_KINDS = ("fw", "mine", "ops", "runbot")
FOR_SEB = ("✅", "✋")
KINDS = ("ops", "fw", "runbot", "task", "mine", "analysis", "review")
LIVE = ("✅", "✋", "⏳")
LOADING = "🔄"
MARKS = (
    (FAILED, "failed"),
    ("✅", "finished"),
    ("✋", "waiting"),
    ("⏳", "running"),
    ("🚀", "starting"),
)
NEW_OPS = "➕ new ops agent"
NO_MARK = "🪟"
OPENING = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "bundle-ctl" / "opening"
OPENING_TIME = 300
PRIORITY = (FAILED, "✋", "✅", "🚀", "⏳")
QUIET = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
Row = collections.namedtuple("Row", "place rank title key window minimized ended", defaults=[""])
WAIT = b"Press Enter to close."
XCB_ATOM_ATOM = 4

close_by_session = {}


class AtomReply(ctypes.Structure):
    _fields_ = [
        ("response_type", ctypes.c_uint8),
        ("pad0", ctypes.c_uint8),
        ("sequence", ctypes.c_uint16),
        ("length", ctypes.c_uint32),
        ("atom", ctypes.c_uint32),
    ]


class Cookie(ctypes.Structure):
    _fields_ = [("sequence", ctypes.c_uint)]
SKIPPED = ("nemo-desktop.",)


@functools.cache
def load_title():
    spec = importlib.util.spec_from_file_location("title", HERE / "client" / "window-title.py")
    title = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(title)
    return title


def get_title_patterns():
    """The pattern of a bundle title, and the one of an ops title, whose conversation title can
    hold any word."""
    title = load_title()
    marks = "|".join(re.escape(mark) for mark, _ in MARKS)
    kinds = [(icon, kind) for icon, kind, _ in title.KINDS if kind != "ops"]
    icons = "|".join(re.escape(icon) for icon, _ in kinds)
    words = "|".join(kind for _, kind in kinds)
    ops = next(icon for icon, kind, _ in title.KINDS if kind == "ops")
    return (
        re.compile(rf"(?:(?:{marks}) )?(?:{icons}) (\S+) \((?:{words})\)"),
        re.compile(rf"(?:(?:{marks}) )?{re.escape(ops)} .+ \(ops\)"),
    )


def get_rank(title):
    ranks = (rank for rank, (mark, _) in enumerate(MARKS) if title.startswith(mark))
    return next(ranks, len(MARKS))


def get_order(title):
    orders = (order for order, kind in enumerate(KINDS) if title.endswith(f"({kind})"))
    return next(orders, len(KINDS))


def get_place(title):
    rank, order = get_rank(title), get_order(title)
    if title.startswith(FOR_SEB) and order < len(KINDS) and KINDS[order] in FIRST_KINDS:
        return (1, order, rank)
    return (0 if title.startswith(FAILED) else 2, rank, order)


def get_agent_bundles():
    """The bundles whose claude runs in a terminal: agent-terminal.py BUNDLE ends with it. The
    agent-terminator.py of the window holds the same words as COMMAND for as long as the window
    lives. The command lines are read as bytes and most are left at once: a thousand processes
    read as text took 70 ms, every 2 seconds.

    Also the end each agent window shows once its claude ended, by the pid of its terminator: the
    keep_open of agent-terminal.py then runs `sh -c WAIT ENDING` in the pane, until Enter."""
    bundles, ended = set(), {}
    for pid in os.listdir("/proc"):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as file:
                data = file.read()
        except OSError:
            continue
        if data.startswith(b"sh\0-c\0") and WAIT in data:
            try:
                stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
            except OSError:
                continue
            ended[stat.rpartition(")")[2].split()[1]] = data.decode(errors="replace").split("\0")[3]
            continue
        if b"agent-terminal.py" not in data:
            continue
        args = data.decode(errors="replace").split("\0")
        scripts = [at for at, arg in enumerate(args) if arg.endswith("agent-terminal.py")]
        if scripts and not any(arg.endswith("agent-terminator.py") for arg in args):
            bundles.add(args[scripts[0] + 1])
    return bundles, ended


def get_loading(name):
    try:
        opening = time.time() - (OPENING / name).stat().st_mtime < OPENING_TIME
    except OSError:
        opening = False
    return f"{LOADING} " if opening else ""


def get_windows():
    """The windows of each bundle, by kind, with the title of its row, the ops agent windows, and
    the other windows."""
    out = subprocess.run(["wmctrl", "-l", "-x"], capture_output=True, check=False, text=True)
    pattern, ops_pattern = get_title_patterns()
    bundles, ops, others = {}, [], []
    for line in out.stdout.splitlines():
        window, _desktop, wm_class, _host, title = (line.split(None, 4) + [""])[:5]
        if not title or wm_class.startswith(SKIPPED):
            continue
        if wm_class.startswith("terminator.") and ops_pattern.fullmatch(title):
            ops.append((title, window))
            continue
        match = pattern.match(title)
        if not match:
            others.append((title, window))
            continue
        kind = "code" if wm_class.startswith("code.") else "terminal"
        bundle = bundles.setdefault(match[1], {})
        bundle[kind] = window
        if kind == "code" or "title" not in bundle:
            bundle["title"] = match[0]
    return bundles, ops, others


@functools.cache
def load_xcb():
    xcb, libc = ctypes.CDLL("libxcb.so.1"), ctypes.CDLL(None)
    pointer, word = ctypes.c_void_p, ctypes.c_uint32
    for name, result, args in (
        ("xcb_connect", pointer, [ctypes.c_char_p, pointer]),
        ("xcb_connection_has_error", ctypes.c_int, [pointer]),
        ("xcb_disconnect", None, [pointer]),
        ("xcb_get_property", Cookie, [pointer, ctypes.c_uint8, word, word, word, word, word]),
        ("xcb_get_property_reply", pointer, [pointer, Cookie, pointer]),
        ("xcb_get_property_value", ctypes.POINTER(word), [pointer]),
        ("xcb_get_property_value_length", ctypes.c_int, [pointer]),
        ("xcb_intern_atom", Cookie, [pointer, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_char_p]),
        ("xcb_intern_atom_reply", ctypes.POINTER(AtomReply), [pointer, Cookie, pointer]),
    ):
        function = getattr(xcb, name)
        function.restype, function.argtypes = result, args
    libc.free.argtypes = [pointer]
    return xcb, libc


def get_hidden(windows):
    """The minimized windows among these ids, whose _NET_WM_STATE holds _NET_WM_STATE_HIDDEN. One X
    connection asks for all of them before it reads the first answer: an xprop per window took
    130 ms for 52 windows, every 2 seconds."""
    xcb, libc = load_xcb()
    connection = xcb.xcb_connect(None, None)
    try:
        if xcb.xcb_connection_has_error(connection):
            return set()
        names = (b"_NET_WM_STATE", b"_NET_WM_STATE_HIDDEN")
        cookies = [xcb.xcb_intern_atom(connection, 0, len(name), name) for name in names]
        atoms = []
        for cookie in cookies:
            reply = xcb.xcb_intern_atom_reply(connection, cookie, None)
            if not reply:
                return set()
            atoms.append(reply.contents.atom)
            libc.free(reply)
        state, hidden = atoms
        cookies = [
            xcb.xcb_get_property(connection, 0, int(window, 16), state, XCB_ATOM_ATOM, 0, 32)
            for window in windows
        ]
        found = set()
        for window, cookie in zip(windows, cookies):
            reply = xcb.xcb_get_property_reply(connection, cookie, None)
            if not reply:
                continue
            count = xcb.xcb_get_property_value_length(reply) // 4
            if hidden in xcb.xcb_get_property_value(reply)[:count]:
                found.add(window)
            libc.free(reply)
        return found
    finally:
        xcb.xcb_disconnect(connection)


def get_rows(windows, shown=False):
    """The rows of the bundles not open in VS Code and of the ops agent windows, in their order:
    the minimized ones, or all of them with shown. The key of a row is its bundle, or its window
    for an ops agent."""
    bundles, ops, _others = windows
    for name in [name for name, bundle in bundles.items() if "code" in bundle]:
        (OPENING / name).unlink(missing_ok=True)
        del bundles[name]
    agents, ended = get_agent_bundles()
    pids = read_window_pids() if ended else {}
    hidden = get_hidden([bundle["terminal"] for bundle in bundles.values()] + [w for _, w in ops])
    rows = []
    for name, bundle in bundles.items():
        title, window = bundle["title"], bundle["terminal"]
        if title.startswith(LIVE) and name not in agents:
            title = title.split(" ", 1)[1]
        ending = ended.get(pids.get(window))
        rows.append(get_row(title, name, window, window in hidden, ending, get_loading(name)))
    for title, window in ops:
        rows.append(get_row(title, window, window, window in hidden, ended.get(pids.get(window))))
    return sorted(row for row in rows if shown or row.minimized)


def get_row(title, key, window, minimized, ending=None, loading=""):
    """The row of a window. One whose claude ended and that waits for Enter is failed, and keeps
    the end it shows without the name before it."""
    if ending:
        title = f"{FAILED} {title.split(' ', 1)[1] if get_rank(title) < len(MARKS) else title}"
    ended = ending.partition(": ")[2] if ending else ""
    return Row(get_place(title), get_rank(title), loading + title, key, window, minimized, ended)


def get_counts(rows):
    ranks = [row.rank for row in rows]
    return [f"{mark} {word} {ranks.count(rank)}" for rank, (mark, word) in enumerate(MARKS)]


def read_window_pids():
    out = subprocess.run(["wmctrl", "-l", "-p"], capture_output=True, check=False, text=True)
    return {line.split()[0]: line.split()[2] for line in out.stdout.splitlines()}


def get_terminator(pid):
    """The agent-terminator.py process whose terminator is pid, and the bundle of its window, None
    for another window."""
    try:
        parent = pathlib.Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[1]
        args = pathlib.Path(f"/proc/{parent}/cmdline").read_text(errors="replace").split("\0")
        script = next(at for at, arg in enumerate(args) if arg.endswith("agent-terminator.py"))
        return int(parent), args[script + 1]
    except (IndexError, OSError, StopIteration):
        return None


def get_session(row, pids):
    """The session of a row: the one in the .agent/ folder of a bundle, the one an ops window is
    named after."""
    if row.key != row.window:
        return read_text(get_agent_folder(row.key) / "session")
    terminator = get_terminator(pids.get(row.window))
    return terminator and terminator[1].removeprefix(OPS)


def get_close_line(session):
    """The close line of the last answer of a session, read again only once its transcript
    changed, as agent-list.py asks every 2 seconds."""
    title = load_title()
    path = session and title.get_transcript(session)
    try:
        stat = path.stat()
    except (AttributeError, OSError):
        return ""
    stamp = (stat.st_mtime_ns, stat.st_size)
    if close_by_session.get(session, (None,))[0] != stamp:
        close_by_session[session] = (stamp, title.get_close_line(title.get_last_answer(session)))
    return close_by_session[session][1]


def get_close_lines(rows):
    """The close line of the session of each row, by key, "" when it has none."""
    pids = read_window_pids() if any(row.key == row.window for row in rows) else {}
    return {row.key: get_close_line(get_session(row, pids)) for row in rows}


def pick(name, terminal):
    windows = get_windows()[0].get(name, {})
    window = (terminal and windows.get("terminal")) or windows.get("code")
    if window:
        subprocess.run(["wmctrl", "-i", "-a", window], check=False)
    else:
        OPENING.mkdir(parents=True, exist_ok=True)
        (OPENING / name).touch()
        subprocess.Popen(["xdg-open", f"odoo-bundle://{name}"], start_new_session=True, **QUIET)


def open_ops():
    command = [sys.executable, str(HERE / "client" / "bctl.py"), "open", "ops", "--terminal"]
    subprocess.Popen(command, start_new_session=True, **QUIET)


def get_terminal(name):
    """The agent window of a bundle, or of an ops session (ops-SESSION), None when it has none."""
    if not name.startswith(OPS):
        return get_windows()[0].get(name, {}).get("terminal")
    pids = read_window_pids()
    for _title, window in get_windows()[1]:
        terminator = get_terminator(pids.get(window))
        if terminator and terminator[1] == name:
            return window
    return None


def is_in_tab(name):
    """Whether a claude of the ops container runs the session of name out of an agent window: in
    a tab of the ops window."""
    if not HOST_FOLDER:
        return False
    records = pathlib.Path(CLAUDE_CONFIG_CONTAINER) / os.path.basename(HOST_FOLDER) / "sessions"
    return name.removeprefix(OPS) in get_live_sessions([records])


def bring_up_agent(name, code=True):
    if name.startswith(OPS):
        window = get_terminal(name)
        if not window and is_in_tab(name):
            subprocess.Popen(["xdg-open", "odoo-bundle://ops"], start_new_session=True, **QUIET)
            return 0
    else:
        windows = get_windows()[0].get(name, {})
        window = windows.get("terminal") or (windows.get("code") if code else None)
    if not window:
        command = [sys.executable, str(HERE / "client" / "bctl.py"), "open", name, "--terminal"]
        if code := subprocess.run(command, check=False).returncode:
            return code
        deadline = time.monotonic() + AGENT_WAIT
        while not (window := get_terminal(name)):
            if time.monotonic() > deadline:
                return 0
            time.sleep(1)
    subprocess.run(["wmctrl", "-i", "-a", window], check=False)
    return 0


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--agent":
        return bring_up_agent(sys.argv[2])
    if len(sys.argv) == 3 and sys.argv[1] == "--terminal":
        return bring_up_agent(sys.argv[2], code=False)
    if len(sys.argv) == 3 and sys.argv[1] == "--code":
        return pick(sys.argv[2], terminal=False)
    if sys.argv[1:] == ["--show"]:
        mode = f"windows:{pathlib.Path(__file__).resolve()}"
        click = ["-hover-select", "-me-select-entry", "", "-me-accept-entry", "MousePrimary"]
        terminal = ["-kb-accept-alt", "", "-kb-custom-1", "Shift+Return,MouseSecondary"]
        os.execvp("rofi", ["rofi", "-modi", mode, "-show", "windows", *click, *terminal])
    retv = os.environ.get("ROFI_RETV")
    if retv in ("1", "10"):
        info = os.environ["ROFI_INFO"]
        if info == "new-ops":
            open_ops()
        elif info.startswith("0x"):
            subprocess.run(["wmctrl", "-i", "-a", info], check=False)
        else:
            pick(info, terminal=retv == "10")
        return
    windows = get_windows()
    rows = get_rows(windows)
    counts = get_counts(rows)
    if sys.argv[1:] == ["--counts"]:
        ranks = [row.rank for row in rows]
        found = {mark: ranks.count(rank) for rank, (mark, _) in enumerate(MARKS)}
        label = next((f"{mark} {found[mark]}" for mark in PRIORITY if found[mark]), NO_MARK)
        print(f"{label}\n{'   '.join(counts)}")
        return
    hint = "click: VS Code   right click: terminal"
    sys.stdout.write(f"\0message\x1f{'   '.join(counts)}   |   {hint}\n")
    sys.stdout.write("\0no-custom\x1ftrue\n\0use-hot-keys\x1ftrue\n")
    sys.stdout.write(f"{NEW_OPS}\0info\x1fnew-ops\n")
    closes = get_close_lines(rows)
    for row in rows:
        word = MARKS[row.rank][1] if row.rank < len(MARKS) else ""
        text = f"{row.title}   {closes[row.key]}".rstrip()
        sys.stdout.write(f"{text}\0info\x1f{row.key}\x1fmeta\x1f{word}\n")
    for title, window in sorted(windows[2]):
        sys.stdout.write(f"{title}\0info\x1f{window}\x1fmeta\x1f\n")


if __name__ == "__main__":
    sys.exit(main())
