#!/usr/bin/env python3
"""Where the CPU of the host went, from the minute lines of cpulog.py in the journal of
bundle-ctl:

    cpu-report.py [--hours N] [--end "YYYY-MM-DD HH:MM"]

The last N hours (24) up to --end (now), in UTC. Each figure is in threads busy on average over
the minutes logged. A bundle is counted by its kind (window-title.py) and by what it did that
minute: `test` with a run in the run queue, `agent` with its agent at work and no run, `idle`
with neither (a server left running, a window of Seb).
"""

import argparse
import collections
import datetime
import importlib.util
import json
import os
import pathlib
import subprocess

CHUNK = datetime.timedelta(hours=2)
HERE = pathlib.Path(__file__).resolve().parent
ROWS = 12


def load_title():
    spec = importlib.util.spec_from_file_location("title", HERE / "client" / "window-title.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_lines(start, end):
    """The cpu lines of the journal, read in CHUNK pieces: hctl cuts an output over 1 MB."""
    lines = []
    while start < end:
        until = min(start + CHUNK, end)
        command = [
            "journalctl",
            "--user",
            "-u",
            "bundle-ctl",
            "-o",
            "short-iso",
            "--no-pager",
            "-g",
            "^cpu \\{",
            "--since",
            f"{start:%Y-%m-%d %H:%M:%S} UTC",
            "--until",
            f"{until:%Y-%m-%d %H:%M:%S} UTC",
        ]
        out = subprocess.run(command, capture_output=True, check=False, text=True).stdout
        lines += out.splitlines()
        start = until
    return lines


def parse(lines):
    """Each logged minute: its time and its line."""
    entries = []
    for line in lines:
        stamp, _, rest = line.partition(" ")
        _, found, data = rest.partition(": cpu ")
        if not found:
            continue
        try:
            at = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z")
            entries.append((at, json.loads(data)))
        except ValueError:
            continue
    return entries


def get_kind(name, suffix, kinds):
    if name == "ops" or name.startswith("gateway:"):
        return name.partition(":")[0]
    own = name.endswith(suffix)
    return next(kind for _icon, kind, matches in kinds if matches(name.removesuffix(suffix), own))


def get_command(args):
    """A process by its program, and its first word when that is no option: `git log`,
    `python3 hokwin.py`."""
    words = args.split()
    if not words:
        return "?"
    name = os.path.basename(words[0])
    if len(words) > 1 and not words[1].startswith("-"):
        name += f" {os.path.basename(words[1])}"
    return name


def sum_up(entries, suffix):
    """The averages over the minutes of entries."""
    kinds_of_bundles = load_title().KINDS
    minutes = len(entries) or 1
    totals = {
        "busy": 0.0,
        "bundles": collections.Counter(),
        "commands": collections.Counter(),
        "cost": 0.0,
        "cpus": 0,
        "hours": collections.defaultdict(list),
        "host": collections.Counter(),
        "kinds": collections.Counter(),
        "minutes": len(entries),
        "uses": collections.defaultdict(collections.Counter),
        "wait": 0.0,
        "where": collections.defaultdict(collections.Counter),
    }
    for at, data in entries:
        totals["busy"] += data["busy"] / minutes
        totals["wait"] += data["wait"] / minutes
        totals["cost"] += data.get("cost", 0) / minutes
        totals["cpus"] = max(totals["cpus"], data.get("cpus", 0))
        totals["hours"][at.strftime("%m-%d %H:00")].append((data["busy"], data["wait"]))
        for name, threads in data["containers"].items():
            kind = get_kind(name, suffix, kinds_of_bundles)
            use = ""
            if kind not in ("ops", "gateway"):
                use = "test" if name in data["runs"] else "agent" if name in data["agents"] else "idle"
                totals["uses"][name][use] += threads / minutes
            totals["kinds"][kind, use] += threads / minutes
            totals["bundles"][name] += threads / minutes
        for name, threads in data["host"].items():
            totals["host"][name] += threads / minutes
        for threads, where, args in data["top"]:
            command = get_command(args)
            totals["commands"][command] += threads / minutes
            totals["where"][command][where] += threads
    for name in totals["kinds"]:
        totals["kinds"][name] = round(totals["kinds"][name], 2)
    return totals


def print_rows(title, rows, busy):
    print(f"\n{title[:60]:<62}threads share")
    for label, threads in rows[:ROWS]:
        print(f"  {label:<60} {threads:6.2f} {threads / busy if busy else 0:5.0%}")


def report(totals):
    busy = totals["busy"]
    of = f" of {totals['cpus']}" if totals["cpus"] else ""
    print(
        f"{totals['minutes']} minutes logged: {busy:.1f} threads busy{of} on average, tasks waited"
        f" on the CPU {totals['wait']:.0f}% of the time; the log took {totals['cost']:.3f} s of"
        " CPU a minute"
    )
    parts = [(f"{kind} {use}".strip(), threads) for (kind, use), threads in totals["kinds"].items()]
    parts += [(f"host: {name}", threads) for name, threads in totals["host"].items()]
    print_rows("Where it went", sorted(parts, key=lambda row: -row[1]), busy)
    rows = []
    for name, threads in totals["bundles"].most_common():
        uses = totals["uses"].get(name)
        split = ", ".join(f"{use} {value / threads:.0%}" for use, value in uses.most_common()) if uses else ""
        rows.append((f"{name[:40]} {split}".rstrip(), threads))
    print_rows("Containers, the busiest, by test / agent / idle", rows, busy)
    rows = [
        (f"{command[:30]} in {totals['where'][command].most_common(1)[0][0][:28]}", threads)
        for command, threads in totals["commands"].most_common()
    ]
    print_rows("Processes over 3 s in a minute, the busiest", rows, busy)
    print("\nHours: threads busy, % waited on the CPU")
    for hour, values in sorted(totals["hours"].items()):
        mean = [sum(column) / len(values) for column in zip(*values, strict=True)]
        print(f"  {hour}  {mean[0]:5.1f}  {mean[1]:3.0f}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--end", help="YYYY-MM-DD HH:MM in UTC, now by default")
    args = parser.parse_args()
    end = datetime.datetime.now(datetime.timezone.utc)
    if args.end:
        end = datetime.datetime.strptime(args.end, "%Y-%m-%d %H:%M").replace(tzinfo=end.tzinfo)
    entries = parse(read_lines(end - datetime.timedelta(hours=args.hours), end))
    if not entries:
        print("no cpu line in the journal of bundle-ctl over that time")
        return
    suffix = os.environ.get("BUNDLE_SUFFIX") or f"--{os.environ.get('USER', 'odoo')}"
    report(sum_up(entries, suffix))


if __name__ == "__main__":
    main()
