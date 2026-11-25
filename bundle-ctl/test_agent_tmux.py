#!/usr/bin/env python3
"""agent-tmux.py, the holder of an agent with no window, with a fake tmux: the pane it starts with
the agent's command and environment, the note it keeps and drops, how it ends on SIGUSR1 and when
a tab of VS Code takes the session, and that it loads with the standard library alone:
python3 test_agent_tmux.py"""

import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
BUNDLE = "master-x--seb"
OPS = "ops-report"
TMUX = """\
#!/usr/bin/env python3
import os, pathlib, signal, subprocess, sys
socket, rest = pathlib.Path(sys.argv[2]), sys.argv[3:]
if rest[0] == "-f":
    rest = rest[2:]
pid = socket.with_name(socket.name + ".pid")
with open(socket.parent / "log", "a") as log:
    log.write(" ".join(rest[:7]) + "\\n")
if rest[0] == "new-session":
    socket.parent.mkdir(parents=True, exist_ok=True)
    pid.write_text(str(subprocess.Popen(rest[8:], start_new_session=True).pid))
elif rest[0] == "display-message":
    print(pid.read_text())
elif rest[0] == "kill-server":
    os.kill(int(pid.read_text()), signal.SIGHUP)
"""


def load(root):
    spec = importlib.util.spec_from_file_location("holder", HERE / "agent-tmux.py")
    holder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(holder)
    agents = sys.modules["agents"]
    agents.WINDOWS = root / "windows"
    agents.TMUX = root / "tmux"
    fake = root / "fake-tmux"
    fake.write_text(TMUX)
    fake.chmod(0o755)
    holder.TMUX = str(fake)
    holder.CHECK = 0.1
    holder.CLAUDE_CONFIG = root / "records"
    holder.HOST_FOLDER = "/repo/host-ctl"
    holder.is_host_stopping = lambda: False
    holder.get_agent_folder = lambda name: root / name / ".agent"
    return holder


def run(holder, name, code, during=None):
    """main of the holder on a pane that runs code, with during run aside: the environment the pane
    saw, the note while it ran, whether the note stays, and the tmux verbs."""
    root = holder.CLAUDE_CONFIG.parent
    seen = root / "seen.json"
    seen.unlink(missing_ok=True)
    (root / "tmux" / "log").unlink(missing_ok=True)
    keys = ("BUNDLE_CTL_AGENT_WINDOW", "BUNDLE_CTL_WINDOW_PID", "BUNDLE_CTL_AGENT_COMMAND")
    probe = (
        "import json, os, sys, time;"
        f"json.dump({{k: os.environ.get(k) for k in {keys!r}}} | {{'argv': sys.argv[1:]}},"
        f" open({str(seen)!r}, 'w'));"
        f"{code}"
    )
    notes = []
    if during:
        threading.Timer(0.5, during).start()
    threading.Timer(0.2, lambda: notes.append((root / "windows" / f"{name}.json").exists())).start()
    sys.argv[1:] = [name, sys.executable, "-c", probe, name]
    holder.closing.clear()
    holder.main()
    log = (root / "tmux" / "log").read_text().split("\n")
    return (
        json.loads(seen.read_text()),
        notes,
        (root / "windows" / f"{name}.json").exists(),
        [line.split()[0] for line in log if line],
    )


def check_loaded():
    load = (
        "import importlib.util as util;"
        f"spec = util.spec_from_file_location('holder', {str(HERE / 'agent-tmux.py')!r});"
        "spec.loader.exec_module(util.module_from_spec(spec))"
    )
    found = subprocess.run([sys.executable, "-S", "-c", load], capture_output=True, check=False, text=True)
    return [f"loads with the standard library alone: {found.stderr.strip()[-200:]}"] if found.returncode else []


def check_records(holder):
    """A record whose fields have the wrong types is left out (security check 11-03, B21)."""
    sessions = holder.CLAUDE_CONFIG / BUNDLE / "sessions"
    sessions.mkdir(parents=True)
    for name, record in (
        ("1", {"name": BUNDLE, "sessionId": "s1", "startedAt": "late"}),
        ("2", {"name": BUNDLE, "sessionId": ["s2"], "startedAt": 5}),
        ("3", {"name": BUNDLE, "sessionId": "s3", "startedAt": 4}),
    ):
        (sessions / f"{name}.json").write_text(json.dumps(record))
    found = holder.get_record(BUNDLE, 0)
    for path in sessions.iterdir():
        path.unlink()
    return [] if (found or {}).get("sessionId") == "s3" else [f"record {found!r}, expected s3"]


def check_runs(holder):
    failures = []
    pid = str(os.getpid())
    env, notes, kept, verbs = run(holder, BUNDLE, "time.sleep(0.4)")
    expected = {
        "BUNDLE_CTL_AGENT_WINDOW": BUNDLE,
        "BUNDLE_CTL_WINDOW_PID": pid,
        "BUNDLE_CTL_AGENT_COMMAND": None,
        "argv": [BUNDLE],
    }
    if env != expected:
        failures.append(f"the pane saw {env!r}, expected {expected!r}")
    if (notes, kept, verbs) != ([True], False, ["new-session", "display-message"]):
        failures.append(f"an agent that ends: note {notes!r}, kept {kept}, tmux {verbs!r}")
    started = time.monotonic()
    _env, _notes, kept, verbs = run(
        holder, BUNDLE, "time.sleep(30)", lambda: os.kill(os.getpid(), signal.SIGUSR1)
    )
    if kept or verbs[-1:] != ["kill-server"] or time.monotonic() - started > 10:
        failures.append(f"SIGUSR1: kept {kept}, tmux {verbs!r}")
    ops = holder.CLAUDE_CONFIG / "host-ctl" / "sessions"
    ops.mkdir(parents=True)

    def open_tab():
        tab = {"entrypoint": "claude-vscode", "sessionId": "report", "startedAt": time.time() * 1000}
        (ops / "1.json").write_text(json.dumps(tab))

    (holder.CLAUDE_CONFIG.parent / OPS / ".agent").mkdir(parents=True)
    _env, _notes, kept, verbs = run(holder, OPS, "time.sleep(30)", open_tab)
    gone = not (holder.CLAUDE_CONFIG.parent / OPS / ".agent").exists()
    if kept or verbs[-1:] != ["kill-server"] or not gone:
        failures.append(f"a tab on the ops session: kept {kept}, tmux {verbs!r}, folder gone {gone}")
    files = list((holder.CLAUDE_CONFIG.parent / "tmux").glob("*.json"))
    if files:
        failures.append(f"holder files left once their agent ended: {files!r}")
    holder.is_host_stopping = lambda: True
    _env, _notes, kept, _verbs = run(holder, BUNDLE, "pass")
    if not kept:
        failures.append("an agent a shutdown ended dropped its note")
    holder.is_host_stopping = lambda: False
    fake = holder.TMUX
    holder.TMUX = "false"
    sys.argv[1:] = [BUNDLE, sys.executable, "-c", "pass"]
    holder.closing.clear()
    holder.main()
    holder.TMUX = fake
    if (holder.CLAUDE_CONFIG.parent / "windows" / f"{BUNDLE}.json").exists():
        failures.append("a tmux that does not start left the note of its agent")
    socket = sys.modules["agents"].get_tmux_socket("b" * 100, 4194303)
    if len(f"/run/user/1000/bundle-ctl/tmux/{socket.name}") > 107:
        failures.append(f"the socket of a 100-character bundle is too long: {socket.name}")
    return failures


def check_running():
    """is_agent_running takes agent-terminal.py BUNDLE, not the agent-tmux.py that holds it."""
    script = str(HERE / "agent-terminal.py")
    check = (
        f"import sys; sys.path.insert(0, {str(HERE.parent / 'scripts')!r}); import agents;"
        "print(agents.is_agent_running(sys.argv[-1]))"
    )
    sleep = [sys.executable, "-c", "import time; time.sleep(60)"]
    failures = []
    for command, name, expected in (([*sleep, script, "ops-a"], "ops-a", "True"), (None, "ops-c", "False")):
        agent = command and subprocess.Popen(command)
        time.sleep(0.2)
        held = [sys.executable, "-c", check, str(HERE / "agent-tmux.py"), name, script, name]
        found = subprocess.run(held, capture_output=True, check=False, text=True).stdout.strip()
        if agent:
            agent.kill()
            agent.wait()
        if found != expected:
            failures.append(f"is_agent_running {name}: {found!r}, expected {expected!r}")
    return failures


def main():
    failures = check_loaded() + check_running()
    with tempfile.TemporaryDirectory() as root:
        holder = load(pathlib.Path(root))
        failures += check_records(holder) + check_runs(holder)
    for line in failures:
        print(line)
    cases = 1 + 2 + 1 + 9
    print(f"{cases - len(failures)}/{cases} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
