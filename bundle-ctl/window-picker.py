#!/usr/bin/env python3
"""List the bundles not open in VS Code, the ops agent windows and the other windows for rofi, the
bundles and the ops windows first by the mark of their title, then by kind, and bring the one
picked to the front. A ✅ or ✋ row of an ops, fw, runbot or mine agent comes first, by kind then
by mark, as it waits for Seb (Seb, 2026-10-15): a ✋ fw comes after a ✅ fw, before a ✅ runbot.

    window-picker.py --show
    window-picker.py --counts

--show opens rofi on this script, where the row under the mouse is selected. A click on a bundle
brings its VS Code window up, or opens it through the `odoo-bundle://` link of gbs; a right click
or Shift+Enter brings its terminal up, when it has one. A click on another window brings it up. A
bundle whose VS Code window is opening shows 🔄 for up to 5 minutes, until that window takes its
row away: closed later, the window gives the row back without 🔄.

A rofi script: it prints the rows when called with no argument, and rofi calls it again on a pick,
with the bundle or the window id in ROFI_INFO. A bundle has one row, from the title of its terminal
(client/window-title.py), and none once its VS Code window is open: Seb then works there. Nor
has a bundle or an ops agent whose terminal is not minimized: it is in the task bar. The terminal
keeps its last mark once its claude ends, so a ✅, ✋ or ⏳ shows only while that claude runs. Each
mark gets a word that the search matches but the row does not show, as the emoji are hard to type;
the kind is already in words. The line above the rows counts the bundles of each mark. --counts
prints the mark that needs Seb most with its count (PRIORITY), or 🪟 when no bundle has a mark,
then that line, for window-applet/.

A minimized ops agent window has a row of its own, ranked by its mark, and a click brings it up:
its title shows the title of its conversation in place of a name, and agent-terminator.py drops its
mark once its claude ended. The first row, `➕ new ops agent`, opens a new one: bctl open ops
--terminal.
"""

import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import time

FIRST_KINDS = ("fw", "mine", "ops", "runbot")
FOR_SEB = ("✅", "✋")
HERE = pathlib.Path(__file__).resolve().parent
KINDS = ("ops", "fw", "runbot", "task", "mine", "analysis", "review")
LIVE = ("✅", "✋", "⏳")
LOADING = "🔄"
MARKS = (
    ("✅", "finished"),
    ("✋", "waiting"),
    ("❌", "failed"),
    ("⏳", "running"),
    ("🚀", "starting"),
)
NEW_OPS = "➕ new ops agent"
NO_MARK = "🪟"
OPENING = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "bundle-ctl" / "opening"
OPENING_TIME = 300
PRIORITY = ("🚀", "✋", "❌", "✅", "⏳")
QUIET = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
SKIPPED = ("nemo-desktop.",)


def get_title_patterns():
    """The pattern of a bundle title, and the one of an ops title, whose conversation title can
    hold any word."""
    spec = importlib.util.spec_from_file_location("title", HERE / "client" / "window-title.py")
    title = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(title)
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
        return (0, order, rank)
    return (1, rank, order)


def get_agent_bundles():
    """The bundles whose claude runs in a terminal: agent-terminal.py BUNDLE ends with it. The
    agent-terminator.py of the window holds the same words as COMMAND for as long as the window
    lives."""
    bundles = set()
    for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = path.read_text(errors="replace").split("\0")
        except OSError:
            continue
        scripts = [at for at, arg in enumerate(args) if arg.endswith("agent-terminal.py")]
        if scripts and not any(arg.endswith("agent-terminator.py") for arg in args):
            bundles.add(args[scripts[0] + 1])
    return bundles


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


def is_minimized(window):
    command = ["xprop", "-id", window, "_NET_WM_STATE"]
    out = subprocess.run(command, capture_output=True, check=False, text=True)
    return "_NET_WM_STATE_HIDDEN" in out.stdout


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


def main():
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
    bundles, ops, others = get_windows()
    for name in [name for name, bundle in bundles.items() if "code" in bundle]:
        (OPENING / name).unlink(missing_ok=True)
        del bundles[name]
    bundles = {name: bundle for name, bundle in bundles.items() if is_minimized(bundle["terminal"])}
    ops = [(title, window) for title, window in ops if is_minimized(window)]
    agents = get_agent_bundles()
    for name, bundle in bundles.items():
        if bundle["title"].startswith(LIVE) and name not in agents:
            bundle["title"] = bundle["title"].split(" ", 1)[1]
    rows = sorted(
        [
            (
                get_place(bundle["title"]),
                get_rank(bundle["title"]),
                get_loading(name) + bundle["title"],
                name,
            )
            for name, bundle in bundles.items()
        ]
        + [(get_place(title), get_rank(title), title, window) for title, window in ops]
        + [((1, len(MARKS), len(KINDS)), len(MARKS), title, window) for title, window in others]
    )
    titles = [bundle["title"] for bundle in bundles.values()] + [title for title, _ in ops]
    ranks = [get_rank(title) for title in titles]
    counts = [f"{mark} {word} {ranks.count(rank)}" for rank, (mark, word) in enumerate(MARKS)]
    if sys.argv[1:] == ["--counts"]:
        found = {mark: ranks.count(rank) for rank, (mark, _) in enumerate(MARKS)}
        label = next((f"{mark} {found[mark]}" for mark in PRIORITY if found[mark]), NO_MARK)
        print(f"{label}\n{'   '.join(counts)}")
        return
    hint = "click: VS Code   right click: terminal"
    sys.stdout.write(f"\0message\x1f{'   '.join(counts)}   |   {hint}\n")
    sys.stdout.write("\0no-custom\x1ftrue\n\0use-hot-keys\x1ftrue\n")
    sys.stdout.write(f"{NEW_OPS}\0info\x1fnew-ops\n")
    for _, rank, title, info in rows:
        word = MARKS[rank][1] if rank < len(MARKS) else ""
        sys.stdout.write(f"{title}\0info\x1f{info}\x1fmeta\x1f{word}\n")


if __name__ == "__main__":
    main()
