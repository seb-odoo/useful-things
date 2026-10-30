#!/usr/bin/env python3
"""Where POST /tell leaves the line of hok for a session, and that an agent window tells its claude
that it runs in one, with no podman and no daemon: python3 test_tell.py
"""

import importlib.util
import os
import pathlib
import shutil
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


def tell(session, text=TEXT, **body):
    try:
        return daemon.tell(daemon.HOST, {}, {"session": session, "text": text, **body})
    except (daemon.Refused, ValueError) as error:
        return type(error).__name__, str(error)


def tell_bundle(bundle, text=TEXT):
    try:
        return daemon.tell(daemon.HOST, {}, {"bundle": bundle, "text": text})
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
    daemon.get_agent_folder = lambda name: agents.get(name, root / "ops" / name)
    daemon.get_terminals = lambda: {"b1": 1}
    daemon.is_agent_running = running.__contains__
    daemon.TOLD_DIR = root / "told"
    host = root / "told" / "b1" / BUNDLE_SESSION

    found = tell(OPS_SESSION)
    check("an ops window is told", found == (200, {"bundle": f"ops-{OPS_SESSION}"}), found)
    check("its line waits in its folder", get_lines(agents[f"ops-{OPS_SESSION}"]) == [TEXT])
    found = tell(STOPPED_SESSION)
    told = get_lines(agents["b2"])
    check("a bundle with no terminal is not", found[0] == 404 and not told, found)

    found = tell(BUNDLE_SESSION)
    check("a bundle terminal is told", found == (200, {"bundle": "b1"}), found)
    check("its line waits in the folder of the host, not in .agent",
          [path.read_text() for path in host.glob("[!.]*.txt")] == [TEXT]
          and not get_lines(agents["b1"]))
    prwatch = load("prwatch", HERE / "client" / "prwatch.py")
    taken = prwatch.take(host)
    check("the terminal takes it as a PR event", taken == [TEXT], taken)
    found = tell_bundle("b1")
    check("a card a gateway held for b1 is told to its session", found == (200, {"bundle": "b1"})
          and prwatch.take(host) == [TEXT], found)
    found = tell_bundle("b2")
    check("a bundle with no terminal is not, by name",
          found[0] == 404 and not get_lines(agents["b2"]), found)
    check("a bad bundle name is refused", tell_bundle("../b1")[0] == "ValueError")
    found = tell(BUNDLE_SESSION, bundle="b1")
    check("a bundle with a session is refused", found[0] == "ValueError", found)
    daemon.reads_told = lambda bundle: False
    found = tell(BUNDLE_SESSION)
    check("a terminal of before told/ gets it in .agent, as then",
          found[0] == 200 and get_lines(agents["b1"]) == [TEXT] and not prwatch.take(host), found)
    shutil.rmtree(agents["b1"] / "pr-events")

    running.clear()
    ops = agents[f"ops-{OPS_SESSION}"]
    found = tell(OPS_SESSION)
    check("an ops window whose claude ended is told", found[0] == 200, found)
    check("its line waits for the window that goes on", get_lines(ops) == [TEXT, TEXT])
    shutil.rmtree(ops)
    found = tell(OPS_SESSION)
    check("a closed ops window is not", found[0] == 404 and not ops.exists(), found)
    found = tell("22222222-2222-3333-4444-555555555555")
    check("a session of no window is not", found[0] == 404, found)
    check("two lines are refused", tell(BUNDLE_SESSION, "a\nb")[0] == "ValueError")
    check("a long line is refused", tell(BUNDLE_SESSION, "a" * 2001)[0] == "ValueError")
    check("a bad session is refused", tell("../b1")[0] == "ValueError")

    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (agents["b1"] / "pr-events").symlink_to(elsewhere)
    found = tell(BUNDLE_SESSION)
    check("a link in place of pr-events is not followed", found[0] == "Refused", found)
    daemon.reads_told = lambda bundle: True
    found = tell(BUNDLE_SESSION)
    check("a link in place of pr-events changes nothing", found[0] == 200, found)
    check("nothing is written where it leads", not any(elsewhere.iterdir()))

    check("only the host asks it", ("POST", "/tell") in daemon.HOST_ONLY)
    check("the verb is served", daemon.VERBS.get(("POST", "/tell")) is daemon.tell)


def ask(verb, session, **body):
    try:
        return verb(daemon.HOST, {}, {"session": session, **body})
    except (daemon.Refused, ValueError) as error:
        return type(error).__name__, str(error)


def check_prompt(root, check):
    """A prompt of deck waits in the folder of the host for the agent named and its session, a
    stop as `stop` there; a bundle that claims the session of another gets neither."""
    agents = {"b4": root / "b4" / ".agent", "b5": root / "b5" / ".agent"}
    for folder in agents.values():
        folder.mkdir(parents=True)
        (folder / "session").write_text(f"{BUNDLE_SESSION}\n")
    daemon.get_agent_folder = agents.__getitem__
    daemon.get_terminals = lambda: {"b4": 1, "b5": 2}
    daemon.is_agent_running = {f"ops-{OPS_SESSION}"}.__contains__
    daemon.get_worktree_bundle_folder = lambda name: str(root / name)
    daemon.DECK_DIR = root / "deck"
    remote = set()
    daemon.remote_agent.is_remote = lambda folder: folder.name in remote

    def prompts(agent, session):
        return sorted(path.read_text() for path in (root / "deck" / agent / session).glob("*.txt"))

    text = f"Pourquoi{chr(0xA0)}? Look again\n\tat the test \U0001f468{chr(0x200D)}\U0001f4bb"
    found = ask(daemon.prompt_window, OPS_SESSION, agent=f"ops-{OPS_SESSION}", text=text)
    check("a prompt to an ops window", prompts(f"ops-{OPS_SESSION}", OPS_SESSION) == [text], found)
    found = ask(daemon.prompt_window, BUNDLE_SESSION, agent="b4", text="go on")
    check("a prompt to a bundle terminal", prompts("b4", BUNDLE_SESSION) == ["go on"], found)
    check("b5, which claims the same session, gets nothing", prompts("b5", BUNDLE_SESSION) == [])
    for agent, session in (("b6", BUNDLE_SESSION), ("b4", STOPPED_SESSION), ("ops-x", OPS_SESSION)):
        found = ask(daemon.prompt_window, session, agent=agent, text="go on")
        check(f"{agent} on {session[:8]} is not told", found[0] == 404, found)
    remote.add("b4")
    found = ask(daemon.prompt_window, BUNDLE_SESSION, agent="b4", text="go on")
    check("an agent on a worker is not told", found[0] == 404 and "worker" in found[1]["error"])
    remote.clear()
    found = ask(daemon.prompt_window, BUNDLE_SESSION, agent="b4", text=" /model opus")
    check("a slash command is refused", found[0] == "Refused", found)
    for bad in ("a\x1b[201~b", "a\rb", "  \n", "a" * (daemon.MAX_TASK + 1)):
        found = ask(daemon.prompt_window, BUNDLE_SESSION, agent="b4", text=bad)
        check(f"a prompt {bad[:8]!r} is refused", found[0] == "ValueError", found)
    found = ask(daemon.prompt_window, BUNDLE_SESSION, agent="../b4", text="go on")
    check("a bad agent is refused", found[0] == "ValueError", found)
    found = ask(daemon.stop_window, BUNDLE_SESSION, agent="b4")
    stop = root / "deck" / "b4" / BUNDLE_SESSION / "stop"
    check("a stop leaves stop", found[0] == 200 and stop.is_file(), found)
    check("nothing goes to the folders of the bundles", not any(root.glob("b*/.agent/prompts")))
    for path in ("/prompt", "/stop"):
        check(f"only the host asks {path}", ("POST", path) in daemon.HOST_ONLY)
    served = (daemon.VERBS.get(("POST", "/prompt")), daemon.VERBS.get(("POST", "/stop")))
    check("the verbs are served", served == (daemon.prompt_window, daemon.stop_window))


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
        check_prompt(root, check)
        check_window(root, check)
        check("no podman ran", not (root / "podman.log").exists())
    for line in failures:
        print(line)
    print(f"{40 - len(failures)}/40 cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
