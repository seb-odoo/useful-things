#!/usr/bin/env python3
"""List every window for rofi, the bundle windows first by the mark of their title, and bring the
one picked to the front.

    window-picker.py --show
    window-picker.py --counts

--show opens rofi on this script, where the row under the mouse is selected and a single click
picks it. A rofi script: it prints the rows when called with no argument, and rofi calls it again
on a pick, with the id of that window in ROFI_INFO. Each mark gets a word that the search matches
but the row does not show, as the emoji are hard to type; the kind of a bundle is already in words
in its title (client/window-title.py). The line above the rows counts the bundles of each mark: the VS
Code window of a bundle carries the same title as its terminal. --counts prints the bundles that
wait for Seb, then that line, for window-applet/.
"""

import os
import pathlib
import subprocess
import sys

MARKS = (
    ("✅", "finished"),
    ("✋", "waiting"),
    ("❌", "failed"),
    ("⏳", "running"),
    ("🚀", "starting"),
)
SKIPPED = ("nemo-desktop.",)


def get_rows():
    out = subprocess.run(["wmctrl", "-l", "-x"], capture_output=True, check=False, text=True)
    rows = []
    for line in out.stdout.splitlines():
        window, _desktop, wm_class, _host, title = (line.split(None, 4) + [""])[:5]
        if not title or wm_class.startswith(SKIPPED):
            continue
        rank = next((rank for rank, (mark, _) in enumerate(MARKS) if title.startswith(mark)), None)
        rows.append((len(MARKS) if rank is None else rank, title, window))
    return sorted(rows)


def get_counts(rows):
    bundles = {(rank, *title.split()[2:3]) for rank, title, _ in rows}
    ranks = [bundle[0] for bundle in bundles]
    return [f"{mark} {word} {ranks.count(rank)}" for rank, (mark, word) in enumerate(MARKS)]


def main():
    if sys.argv[1:] == ["--show"]:
        mode = f"windows:{pathlib.Path(__file__).resolve()}"
        click = ["-hover-select", "-me-select-entry", "", "-me-accept-entry", "MousePrimary"]
        os.execvp("rofi", ["rofi", "-modi", mode, "-show", "windows", *click])
    if os.environ.get("ROFI_RETV") == "1":
        subprocess.run(["wmctrl", "-i", "-a", os.environ["ROFI_INFO"]], check=False)
        return
    rows = get_rows()
    counts = get_counts(rows)
    if sys.argv[1:] == ["--counts"]:
        mark, _word, count = counts[1].split()
        print(f"{mark} {count}\n{'   '.join(counts)}")
        return
    sys.stdout.write(f"\0message\x1f{'   '.join(counts)}\n\0no-custom\x1ftrue\n")
    for rank, title, window in rows:
        word = MARKS[rank][1] if rank < len(MARKS) else ""
        sys.stdout.write(f"{title}\0info\x1f{window}\x1fmeta\x1f{word}\n")


if __name__ == "__main__":
    main()
