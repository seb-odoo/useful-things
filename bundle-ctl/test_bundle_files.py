#!/usr/bin/env python3
"""Each host reader of a file a bundle writes, given a FIFO, a link to /dev/zero and a sparse file
of 8 GB in its place, a file of the scratch folder `root` of READERS: it must give up at once with
nothing, in a process capped at 2 GB of memory and 10 seconds (security check 11-03, B13):
python3 test_bundle_files.py"""

import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
KINDS = ("fifo", "zero", "sparse")
LIMIT = 2 << 30
SPARSE = 8 << 30
READERS = {
    "agents.read_text": ("result.md", "agents.read_text(path)"),
    "agents.get_session_title": ("S.jsonl", "agents.get_session_title(path)"),
    "agents.get_session_records": (
        "c/sessions/1.json",
        "agents.CLAUDE_CONFIG_CONTAINER = str(root); agents.get_session_records('c')",
    ),
    "remote_agent.read_remote": (
        "b/.agent/remote.json",
        "remote_agent.STATE = root; remote_agent.read_remote(root / 'b')",
    ),
    "cut.read_session": ("S.jsonl", "cut.read_session(path)"),
    "cut.get_live_sessions": ("sessions/1.json", "cut.get_live_sessions([root / 'sessions'])"),
    "window-title.read_tail": (
        "S.jsonl",
        "title.get_transcript = lambda *_: path; title.read_tail('S')",
    ),
    "window-title.read_answers": (
        "S.jsonl",
        "title.get_transcript = lambda *_: path; list(title.read_answers('S'))",
    ),
    "agent-terminator.get_record": (
        "b/sessions/1.json",
        "terminator.CLAUDE_CONFIG = root; terminator.get_record('b', 0)",
    ),
    "bundle-info.read_entries": ("S.jsonl", "list(info.read_entries(path))"),
    "agent-list.get_last_prompt": ("S.jsonl", "listing.get_last_prompt(path)"),
    "prwatch.take": ("events/1.txt", "prwatch.take(root / 'events')"),
    "claude-config.read_own": ("settings.json", "claude_config.read_own(path)"),
    "utils._drop_cut_checkout": (
        "t/.git",
        "utils.UtilsRunner._drop_cut_checkout(None, repo_folder=root, target_folder=str(root / 't'))",
    ),
    "remote_agent.keep_regular": (
        "c/settings.json",
        "tarfile.open(fileobj=io.BytesIO(), mode='w').add("
        "root / 'c', filter=lambda info: remote_agent.keep_regular(info, {}))",
    ),
}
RUN = """
import importlib.util, pathlib, resource, sys, types
here, root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
name, call, limit = sys.argv[3], sys.argv[4], int(sys.argv[5])
sys.path[:0] = [str(here), str(here.parent / "scripts")]

def load(module, path):
    spec = importlib.util.spec_from_file_location(module, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded

gi = types.ModuleType("gi")
gi.require_version = lambda *_: None
gi.repository = types.ModuleType("gi.repository")
gi.repository.Gtk = types.SimpleNamespace(Application=object)
gi.repository.Gdk = gi.repository.GdkX11 = gi.repository.Gio = None
gi.repository.GLib = gi.repository.Pango = None
sys.modules.update({"gi": gi, "gi.repository": gi.repository})
import io, tarfile
import agents, cut, remote_agent, utils
claude_config = load("claude_config", here.parent / "devcontainer" / "claude-config.py")
title = load("title", here / "client" / "window-title.py")
terminator = load("terminator", here / "agent-terminator.py")
info = load("info", here / "bundle-info.py")
listing = load("listing", here / "agent-list.py")
prwatch = load("prwatch", here / "client" / "prwatch.py")
path = root / name
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
exec(call)
print("returned")
"""


def plant(path, kind):
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "zero":
        path.symlink_to("/dev/zero")
    else:
        with open(path, "wb") as file:
            file.truncate(SPARSE)


def main():
    failures, cases = [], 0
    for reader, (name, call) in READERS.items():
        for kind in KINDS:
            cases += 1
            with tempfile.TemporaryDirectory() as folder:
                root = pathlib.Path(folder)
                plant(root / name, kind)
                command = [sys.executable, "-c", RUN, str(HERE), folder, name, call, str(LIMIT)]
                try:
                    done = subprocess.run(
                        command, capture_output=True, check=False, text=True, timeout=10
                    )
                except subprocess.TimeoutExpired:
                    failures.append(f"{reader} on a {kind}: still blocked after 10 s")
                    continue
            if done.stdout.strip() != "returned":
                why = (done.stderr.strip().splitlines() or ["no output"])[-1]
                failures.append(f"{reader} on a {kind}: {why}")
    for line in failures:
        print(line)
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
