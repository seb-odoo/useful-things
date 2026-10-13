#!/usr/bin/env python3
"""List the bundles and the other windows for rofi, the bundles first by the mark of their title,
and bring the one picked to the front.

    window-picker.py --show
    window-picker.py --counts

--show opens rofi on this script, where the row under the mouse is selected. A click on a bundle
brings its VS Code window up, or opens it through the `odoo-bundle://` link of gbs; a right click
or Shift+Enter brings its terminal up, when it has one. A click on another window brings it up.

A rofi script: it prints the rows when called with no argument, and rofi calls it again on a pick,
with the bundle or the window id in ROFI_INFO. A bundle has one row, though its terminal and its VS
Code window both carry its title (client/window-title.py): the VS Code one wins, as the window
holds the session once open. Each mark gets a word that the search matches but the row does not
show, as the emoji are hard to type; the kind is already in words. The line above the rows counts
the bundles of each mark. --counts prints the bundles that wait for Seb, then that line, for
window-applet/.
"""

import importlib.util
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
MARKS = (
    ("✅", "finished"),
    ("✋", "waiting"),
    ("❌", "failed"),
    ("⏳", "running"),
    ("🚀", "starting"),
)
SKIPPED = ("nemo-desktop.",)


def get_title_pattern():
    spec = importlib.util.spec_from_file_location("title", HERE / "client" / "window-title.py")
    title = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(title)
    marks = "|".join(re.escape(mark) for mark, _ in MARKS)
    icons = "|".join(re.escape(icon) for icon, _, _ in title.KINDS)
    kinds = "|".join(kind for _, kind, _ in title.KINDS)
    return re.compile(rf"(?:(?:{marks}) )?(?:{icons}) (\S+) \((?:{kinds})\)")


def get_rank(title):
    ranks = (rank for rank, (mark, _) in enumerate(MARKS) if title.startswith(mark))
    return next(ranks, len(MARKS))


def get_windows():
    """The windows of each bundle, by kind, with the title of its row, and the other windows."""
    out = subprocess.run(["wmctrl", "-l", "-x"], capture_output=True, check=False, text=True)
    pattern = get_title_pattern()
    bundles, others = {}, []
    for line in out.stdout.splitlines():
        window, _desktop, wm_class, _host, title = (line.split(None, 4) + [""])[:5]
        if not title or wm_class.startswith(SKIPPED):
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
    return bundles, others


def pick(name, terminal):
    windows = get_windows()[0].get(name, {})
    window = (terminal and windows.get("terminal")) or windows.get("code")
    if window:
        subprocess.run(["wmctrl", "-i", "-a", window], check=False)
    else:
        subprocess.Popen(["xdg-open", f"odoo-bundle://{name}"], start_new_session=True)


def main():
    if sys.argv[1:] == ["--show"]:
        mode = f"windows:{pathlib.Path(__file__).resolve()}"
        click = ["-hover-select", "-me-select-entry", "", "-me-accept-entry", "MousePrimary"]
        terminal = ["-kb-accept-alt", "", "-kb-custom-1", "Shift+Return,MouseSecondary"]
        os.execvp("rofi", ["rofi", "-modi", mode, "-show", "windows", *click, *terminal])
    retv = os.environ.get("ROFI_RETV")
    if retv in ("1", "10"):
        info = os.environ["ROFI_INFO"]
        if info.startswith("0x"):
            subprocess.run(["wmctrl", "-i", "-a", info], check=False)
        else:
            pick(info, terminal=retv == "10")
        return
    bundles, others = get_windows()
    rows = sorted(
        [(get_rank(bundle["title"]), bundle["title"], name) for name, bundle in bundles.items()]
        + [(len(MARKS), title, window) for title, window in others]
    )
    ranks = [get_rank(bundle["title"]) for bundle in bundles.values()]
    counts = [f"{mark} {word} {ranks.count(rank)}" for rank, (mark, word) in enumerate(MARKS)]
    if sys.argv[1:] == ["--counts"]:
        print(f"{MARKS[1][0]} {ranks.count(1)}\n{'   '.join(counts)}")
        return
    hint = "click: VS Code   right click: terminal"
    sys.stdout.write(f"\0message\x1f{'   '.join(counts)}   |   {hint}\n")
    sys.stdout.write("\0no-custom\x1ftrue\n\0use-hot-keys\x1ftrue\n")
    for rank, title, info in rows:
        word = MARKS[rank][1] if rank < len(MARKS) else ""
        sys.stdout.write(f"{title}\0info\x1f{info}\x1fmeta\x1f{word}\n")


if __name__ == "__main__":
    main()
