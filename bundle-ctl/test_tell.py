#!/usr/bin/env python3
"""Where POST /tell leaves the line of hok for a session, and that an agent window tells its claude
that it runs in one, with no podman and no daemon: python3 test_tell.py
"""

import importlib.util
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon  # noqa: E402

OPS_SESSION = "11111111-2222-3333-4444-555555555555"
BUNDLE_SESSION = "66666666-7777-8888-9999-000000000000"
STOPPED_SESSION = "33333333-7777-8888-9999-000000000000"
TEXT = "hok event: Seb answered request 959 (ran)."


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_lines(folder):
    return sorted(path.read_text() for path in folder.glob("pr-events/[!.]*.txt"))


def tell(session, text=TEXT):
    try:
        return daemon.tell(daemon.HOST, {}, {"session": session, "text": text})
    except (daemon.Refused, ValueError) as error:
        return type(error).__name__, str(error)


def check_tell(root, check):
    agents = {f"ops-{OPS_SESSION}": root / "ops" / OPS_SESSION, "b1": root / "b1" / ".agent"}
    agents["b2"] = root / "b2" / ".agent"
    for name in ("b1", "b2"):
        agents[name].mkdir(parents=True)
    (agents["b1"] / "session").write_text(f"{BUNDLE_SESSION}\n")
    (agents["b2"] / "session").write_text(f"{STOPPED_SESSION}\n")
    running = {f"ops-{OPS_SESSION}"}
    daemon.get_agent_folder = agents.__getitem__
    daemon.get_terminals = lambda: {"b1": 1}
    daemon.is_agent_running = running.__contains__

    found = tell(OPS_SESSION)
    check("an ops window is told", found == (200, {"bundle": f"ops-{OPS_SESSION}"}), found)
    check("its line waits in its folder", get_lines(agents[f"ops-{OPS_SESSION}"]) == [TEXT])
    found = tell(STOPPED_SESSION)
    told = get_lines(agents["b2"])
    check("a bundle with no terminal is not", found[0] == 404 and not told, found)

    found = tell(BUNDLE_SESSION)
    check("a bundle terminal is told", found == (200, {"bundle": "b1"}), found)
    check("its line waits in .agent", get_lines(agents["b1"]) == [TEXT])
    prwatch = load("prwatch", HERE / "client" / "prwatch.py")
    taken = prwatch.take(agents["b1"] / "pr-events")
    check("the terminal takes it as a PR event", taken == [TEXT], taken)

    running.clear()
    found = tell(OPS_SESSION)
    check("a closed ops window is not", found[0] == 404, found)
    found = tell("22222222-2222-3333-4444-555555555555")
    check("a session of no window is not", found[0] == 404, found)
    check("two lines are refused", tell(BUNDLE_SESSION, "a\nb")[0] == "ValueError")
    check("a long line is refused", tell(BUNDLE_SESSION, "a" * 2001)[0] == "ValueError")
    check("a bad session is refused", tell("../b1")[0] == "ValueError")

    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    for path in (agents["b1"] / "pr-events").iterdir():
        path.unlink()
    (agents["b1"] / "pr-events").rmdir()
    (agents["b1"] / "pr-events").symlink_to(elsewhere)
    found = tell(BUNDLE_SESSION)
    check("a link in place of pr-events is refused", found[0] == "Refused", found)
    check("nothing is written where it leads", not any(elsewhere.iterdir()))

    check("only the host asks it", ("POST", "/tell") in daemon.HOST_ONLY)
    check("the verb is served", daemon.VERBS.get(("POST", "/tell")) is daemon.tell)


def check_window(root, check):
    """The claude of an ops window and of a bundle terminal gets AGENT_WINDOW, its window."""
    terminal = load("agent_terminal", HERE / "agent-terminal.py")
    commands = []
    terminal.run_tty = lambda command, waker=None: commands.append(command) or 0
    terminal.HOST_FOLDER = str(root / "host-ctl")
    terminal.get_container = lambda folder: "c1"
    terminal.copy_gitconfig = lambda container: None
    terminal.space_start = lambda: None
    terminal.get_agent_folder = lambda name: root / "windows" / name
    terminal.run_ops(f"ops-{OPS_SESSION}")
    agent = root / "windows" / "b3"
    agent.mkdir(parents=True)
    (agent / "task.md").write_text("task")
    terminal.take_over = lambda agent, bundle: ("c1", False)
    terminal.has_window = lambda container: True
    sys.argv = ["agent-terminal.py", "b3"]
    terminal.main()
    found = [command[command.index("c1") - 1] for command in commands]
    expected = [f"AGENT_WINDOW=ops-{OPS_SESSION}", "AGENT_WINDOW=b3"]
    check("each claude knows its window", found == expected, found)


def main():
    failures = []

    def check(name, ok, found=None):
        if not ok:
            failures.append(f"{name}{f': {found!r}' if found is not None else ''}")

    with tempfile.TemporaryDirectory() as folder:
        root = pathlib.Path(folder)
        (root / "bin").mkdir()
        fake = root / "bin" / "podman"
        fake.write_text(f'#!/bin/sh\necho "$*" >>{root}/podman.log\nexit 1\n')
        fake.chmod(0o755)
        os.environ["PATH"] = f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}"
        check_tell(root, check)
        check_window(root, check)
        check("no podman ran", not (root / "podman.log").exists())
    for line in failures:
        print(line)
    print(f"{17 - len(failures)}/17 cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
