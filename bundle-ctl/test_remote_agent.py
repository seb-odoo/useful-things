#!/usr/bin/env python3
"""What a remote agent takes from the laptop: every mount of a bundle has its way in MOUNTS, the
config part holds the config and no login, and the environment leaves out what the worker sets:
python3 test_remote_agent.py

The start, the tunnel, the mirror and the move back run end to end in remote-runs
(`standin/test_standin.py`, `worker/test_agent.py`).
"""

import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import remote_agent

failures = []
total = 0


def check(name, passed, detail=""):
    global total
    total += 1
    if not passed:
        failures.append(f"FAIL {name}: {detail}")


def check_mounts():
    text = (HERE.parent / "devcontainer" / "base.jsonc").read_text()
    start = text.index('"mounts": [')
    targets = re.findall(r'"source=[^,]+,target=([^,]+),', text[start : text.index("]", start)])
    missing = [target for target in targets if target not in remote_agent.MOUNTS]
    check("every mount of a bundle has its way to a remote agent", not missing, missing)
    stale = [target for target in remote_agent.MOUNTS if target not in targets]
    check("and the table names no mount that is gone", not stale, stale)
    ways = {way for way, _why in remote_agent.MOUNTS.values()}
    check("each way is one of five", ways <= {"send", "standin", "image", "job", "drop"}, ways)


def check_config(root):
    home = root / "home"
    claude = home / ".claude"
    (claude / "skills" / "a").mkdir(parents=True)
    (claude / "skills" / "a" / "SKILL.md").write_text("skill\n")
    (claude / "CLAUDE.md").write_text("rules\n")
    (claude / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "secret"}}')
    (claude / "skills" / "a" / "out").symlink_to("/etc/passwd")
    fix_agents = root / "repo" / "runbot-fix-agents"
    (fix_agents / "skills" / "t").mkdir(parents=True)
    (fix_agents / "skills" / "t" / "SKILL.md").write_text("team skill\n")
    (claude / "skills" / "t").symlink_to(fix_agents / "skills" / "t")
    (claude / "skills" / "up").symlink_to(f"{fix_agents}/../secret")
    (root / "useful").mkdir()
    (root / "useful" / ".gitconfig").write_text("[user]\n")
    (home / ".gitconfig").symlink_to(root / "useful" / ".gitconfig")
    own = root / "cache" / "devcontainer" / "claude-config" / "b1"
    own.mkdir(parents=True)
    (own / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "mcpServers": {"x": {"headers": {"Authorization": "t"}}},
        "projects": {"/workspace": {"hasClaudeMdExternalIncludesApproved": True,
                                    "mcpServers": {"y": {}}}},
    }))
    (own / "settings.json").write_text("{}\n")
    shared = root / "worktrees" / ".claude"
    shared.mkdir(parents=True)
    stop = f"python3 {remote_agent.HERE / 'client' / 'agent-stop.py'}"
    (shared / "settings.json").write_text(json.dumps({"hooks": {"Stop": stop}}))
    (shared / "CLAUDE.md").write_text("project\n")
    (own / "project").mkdir()
    for name in ("settings.json", "CLAUDE.md"):
        (own / "project" / name).symlink_to(f"/home/vscode/.claude-project/{name}")
    (own / "project" / "settings.local.json").write_text("{}\n")
    remote_agent.HOME = home
    remote_agent.get_values = lambda: {"CACHE_ROOT": str(root / "cache"),
                                       "REPO_ROOT": str(root / "repo"),
                                       "WORKTREE_ROOT": str(root / "worktrees"),
                                       "SHARED_CLAUDE": str(shared),
                                       "HOST_PROJECT": "-home-seb-repo"}
    data = remote_agent.build_config(root / "b1")
    names, contents, links = [], {}, {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            names.append(member.name)
            if member.isfile():
                contents[member.name] = tar.extractfile(member).read()
            if member.issym():
                links[member.name] = member.linkname
    check("the skills go", "home/.claude/skills/a/SKILL.md" in names, names)
    check("the rules go", contents.get("home/.claude/CLAUDE.md") == b"rules\n", names)
    check("the bundle's settings go", "home/.claude/settings.json" in names, names)
    project = json.loads(contents.get("workspace/.claude/settings.json", b"{}"))
    check("the project hooks run the client the job has",
          project == {"hooks": {"Stop": "python3 /job/home/bundle-ctl/client/agent-stop.py"}},
          project)
    check("once", names.count("workspace/.claude/settings.json") == 1, names)
    check("the project rules, from the shared folder the link names",
          contents.get("workspace/.claude/CLAUDE.md") == b"project\n", names)
    check("and its own files", "workspace/.claude/settings.local.json" in names, names)
    check(
        "the band of the agent terminal goes",
        "home/bundle-ctl/agentbar/.claude-plugin/plugin.json" in names,
        names,
    )
    check("never the login", not any("credentials" in name for name in names), names)
    check("nor a link out of the tree", "home/.claude/skills/a/out" not in names, names)
    check("a link into runbot-fix-agents names its copy",
          links.get("home/.claude/skills/t") == "../../runbot-fix-agents/skills/t", links)
    skill = contents.get("home/runbot-fix-agents/skills/t/SKILL.md")
    check("which goes", skill == b"team skill\n", names)
    check("but not one that climbs out of it", "home/.claude/skills/up" not in names, names)
    check("~/.gitconfig as the file its link names",
          contents.get("home/.gitconfig") == b"[user]\n", names)
    kept = json.loads(contents["home/.claude/.claude.json"])
    check(".claude.json by its allow list",
          kept == {"hasCompletedOnboarding": True,
                   "projects": {"/workspace": {"hasClaudeMdExternalIncludesApproved": True}}},
          kept)
    check("everything under home/ or workspace/",
          all(name.split("/")[0] in ("home", "workspace") for name in names), names)


def check_env(root):
    config = root / "worktrees" / ".devcontainer" / "devcontainer.json"
    config.parent.mkdir(parents=True)
    header = "// Generated by devcontainer/build.py for target x. Do not edit.\n"
    config.write_text(header + json.dumps({"containerEnv": {
        "BASH_ENV": "/x/devcontainer.bashrc", "HTTPS_PROXY": "http://10.89.255.20:3128",
        "https_proxy": "x", "CLAUDE_CONFIG_DIR": "/home/vscode/.claude", "PGHOST": "/run",
        "GIT_SSH_COMMAND": "/opt/git-gate/git-gate-ssh", "GH_TOKEN": "read-only-via-proxy",
        "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
        "BUNDLE_CTL_CLIENT": "/home/seb/repo/useful-things/bundle-ctl/client",
        "ODOO_PROXY_HOST": "${localWorkspaceFolderBasename}",
    }}))
    env = remote_agent.get_env("master-x")
    check("the bundle's helpers go", env.get("BASH_ENV") == "/x/devcontainer.bashrc", env)
    check("and what the gateways read", env.get("GH_TOKEN") == "read-only-via-proxy", env)
    check("bctl as the job has it",
          env.get("BUNDLE_CTL_CLIENT") == "/job/home/bundle-ctl/client", env)
    check("the bundle as firefox-tabs and the hooks read it",
          env.get("ODOO_PROXY_HOST") == "master-x", env)
    left = {"HTTPS_PROXY", "https_proxy", "CLAUDE_CONFIG_DIR", "PGHOST", "GIT_SSH_COMMAND",
            "REQUESTS_CA_BUNDLE"}
    check("not what the worker sets", not left & set(env), env)


def check_remote(root):
    """A remote agent is read from the record of the daemon, no value of another form counts, and
    a remote.json the bundle writes alone is no remote agent (security check 11-03, G1)."""
    agent = root / "bundle" / ".agent"
    agent.mkdir(parents=True)
    remote_agent.STATE = root / "state"
    remote_agent.record_path("bundle").parent.mkdir(parents=True)
    good = {"worker": "sebpc", "job": "1761660000", "dummy": "a" * 64, "started": 1.5,
            "session": "8b9b2f6e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"}
    for name, value, expected in [
        ("as the daemon writes it", good, good),
        ("a new session", dict(good, session=""), dict(good, session="")),
        ("a dummy that names a file of Seb", dict(good, dummy="/home/seb/.bashrc"), None),
        ("a dummy that climbs", dict(good, dummy="../../x"), None),
        ("a worker that climbs", dict(good, worker="../mux"), None),
        ("a job with a word", dict(good, job="1 x"), None),
        ("a key unknown here", dict(good, extra="x"), None),
        ("no job", {"worker": "sebpc"}, None),
        ("a list", ["sebpc"], None),
        ("a start at infinity", dict(good, started=float("inf")), None),
    ]:
        remote_agent.record_path("bundle").write_text(json.dumps(value))
        (agent / "remote.json").write_text(json.dumps(value))
        found = remote_agent.read_remote(root / "bundle")
        check(f"remote.json {name}", found == expected, found)
    (agent / "remote.json").write_text(json.dumps(dict(good, job="2")))
    found = remote_agent.read_remote(root / "bundle")
    check("a remote.json the bundle wrote for another job", found is None, found)
    remote_agent.record_path("bundle").unlink()
    (agent / "remote.json").write_text(json.dumps(good))
    found = remote_agent.read_remote(root / "bundle")
    check("a remote.json the bundle wrote, with no record", found is None, found)


def check_standin(root):
    git_dir = root / "repos" / "odoo" / ".git"
    (git_dir / "hooks").mkdir(parents=True)
    (git_dir / "config").write_text("[core]\n")
    remote_agent.get_values = lambda: {"REPO_ROOT": str(root / "repos"), "CACHE_ROOT": str(root),
                                       "SHARE_ROOT": str(root / "share")}
    remote_agent.HOME = remote_agent.STATE = root
    remote_agent.REMOTE_RUNS = root / "remote-runs"
    runs = []
    remote_agent.subprocess.run = lambda argv, **kwargs: runs.append(argv)
    remote_agent.start_standin("b", root / "bundle", "w", "")
    volumes = set(runs[-1])
    check("the stand-in mounts the base .git read-only", f"{git_dir}:{git_dir}:ro" in volumes, runs[-1])
    for name in ("objects", "refs", "logs", "rr-cache", "packed"):
        mount = f"{git_dir / name}:{git_dir / name}:rw"
        check(f"and its {name} writable over it", mount in volumes, runs[-1])
    written = [volume for volume in volumes if volume.startswith(str(git_dir)) and volume.endswith(":rw")]
    check("nothing else of it", len(written), 6)
    own = git_dir / "worktrees" / "bundle"
    check("but the folder of its worktree", f"{own}:{own}:rw" in volumes, runs[-1])
    admin = remote_agent.HERE.parent / "devcontainer" / "worktree-admin.py"
    named = [sys.executable, str(admin), str(root / "bundle")]
    check("named after the bundle before", named in runs[:-1], runs)
    config = remote_agent.HERE.parent / "devcontainer" / "claude-config.py"
    named = [sys.executable, str(config), "bundle", "--bundle"]
    check("and its Claude folder made as its container makes it", named in runs[:-1], runs)
    transcripts = remote_agent.HERE.parent / "devcontainer" / "transcripts.py"
    named = [sys.executable, str(transcripts), "b", str(root / "bundle")]
    check("and its transcripts folder", named in runs[:-1], runs)
    mirror = f"{root / 'share' / 'claude-transcripts' / 'b'}:/mirror/projects:rw"
    check("its own transcripts mirrored, no other bundle's", mirror in volumes, runs[-1])
    check("the config stays a file", (git_dir / "config").is_file())
    scope = ["systemd-run", "--user", "--scope", "--collect", "--quiet", "podman", "run"]
    check("its conmon out of the cgroup of bundle-ctl", runs[-1][:7] == scope, runs[-1])
    client = f"{remote_agent.HERE / 'client'}:/opt/bundle-ctl:ro"
    named = {
        "ODOO_PROXY_HOST=b",
        "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt",
        "--add-host=gmail.googleapis.com:10.89.255.13",
        "--add-host=api.github.com:10.89.255.10",
        client,
    }
    watch = runs[-1][-3:] == ["sh", "-c", remote_agent.WATCH] and "prwatch.py" in runs[-1][-1]
    check("it watches the PRs of the bundle, as the bundle", watch and named <= volumes, runs[-1])


def check_start(root):
    """A job starts with no record of its session left by an earlier job, as the daemon would read
    it as the status of the claude that does not run yet."""
    remote_agent.get_values = lambda: {"CACHE_ROOT": str(root / "cache"), "SHARE_ROOT": str(root / "share")}
    sessions = root / "cache" / "devcontainer" / "claude-config" / "started" / "sessions"
    sessions.mkdir(parents=True)
    for name, session in (("7", "s1"), ("8", "s2")):
        (sessions / f"{name}.json").write_text(json.dumps({"sessionId": session, "status": "idle"}))
    (sessions / "9.json").write_text("[]")
    (root / "started" / ".agent").mkdir(parents=True)

    def refuse(*args):
        raise RuntimeError("no stand-in")

    remote_agent.start_standin = refuse
    remote_agent.DUMMIES = root
    remote_agent.subprocess.run = lambda argv, **kwargs: None
    try:
        remote_agent.start(
            io.StringIO(), "started", root / "started", "w", None, "18.0", "s1", True
        )
    except RuntimeError:
        pass
    left = sorted(path.name for path in sessions.iterdir())
    check("a job starts with no record of its session", left == ["8.json", "9.json"], left)


def check_binary(root):
    """A binary of the extension stores: the bundle's own, else the newest of any, else none."""
    remote_agent.get_values = lambda: {"CACHE_ROOT": str(root / "cache"), "SHARE_ROOT": str(root / "share")}
    store = root / "cache" / "devcontainer" / "vscode-extensions"
    pattern = "charliermarsh.ruff-*/bundled/libs/bin/ruff"
    found = remote_agent.find_binary(root / "b1", pattern)
    check("no ruff in the stores, none goes", found is None, found)
    paths = {}
    for bundle, mtime in (("b2", 100), ("b3", 200), ("b1", 50)):
        path = store / bundle / "charliermarsh.ruff-2026.92.0-linux-x64/bundled/libs/bin/ruff"
        path.parent.mkdir(parents=True)
        path.write_text("ruff\n")
        os.utime(path, (mtime, mtime))
        paths[bundle] = path
        if bundle == "b3":
            found = remote_agent.find_binary(root / "b1", pattern)
            check("no ruff in its store, the newest of another", found == path, found)
    found = remote_agent.find_binary(root / "b1", pattern)
    check("the ruff of its own store first", found == paths["b1"], found)


def check_type(root):
    """Keys typed on a worker go to the stand-in as one order line, a refusal says why, and the
    mark of a typed task goes with the other files of the job once it ended."""
    runs = []

    def run(argv, input=b"", **kwargs):
        runs.append((argv, input))
        refused = argv[-1] == "order" and b"refused" in input
        error = b"Traceback\nConnectionRefusedError: [Errno 111] Connection refused\n"
        return subprocess.CompletedProcess(argv, int(refused), b"", error if refused else b"")

    remote_agent.subprocess.run = run
    keys = "\x1b[200~fix\nit\x1b[201~"
    found = remote_agent.type_keys("b", keys)
    argv, data = runs[-1]
    order = argv[-2:] == ["/opt/remote-runs/standin/standin.py", "order"]
    check("typed keys are one order of the stand-in", order and data.count(b"\n") == 1, runs[-1])
    sent = json.loads(data) == {"op": "type", "text": keys}
    check("as the worker reads it", sent and found is None, data)
    found = remote_agent.type_keys("b", "refused")
    refusal = "ConnectionRefusedError: [Errno 111] Connection refused"
    check("a stand-in that does not take them says why", found == refusal, found)
    agent = root / "typed" / ".agent"
    agent.mkdir(parents=True)
    (agent / "remote.json").write_text(json.dumps({"worker": "sebpc", "job": "1"}))
    (agent / "remote-ended").write_text("{}\n")
    (agent / "remote-typed").write_text("1000\n")
    remote_agent.DUMMIES = remote_agent.STATE = root
    remote_agent.keep_record("typed", {"worker": "sebpc", "job": "1"})
    remote_agent.finish("typed", root / "typed", {"reason": "verdict"})
    left = sorted(path.name for path in agent.iterdir())
    check("the mark of a typed task goes with the job", left == [], left)


def check_record(root):
    """The daemon keeps a copy of remote.json out of the bundle folder: its start makes it, a pass
    keeps it, the end of the job drops it, and drop_orphan removes what a job no bundle has left
    here. A pass writes no file through a link the bundle left (security check 11-03, G1)."""
    remote_agent.STATE = remote_agent.DUMMIES = root / "record"
    runs = []

    def run(argv, **kwargs):
        runs.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    remote_agent.subprocess.run = run
    workers = types.SimpleNamespace(is_up=lambda name: False, ask=lambda name: None)
    agent = root / "kept" / ".agent"
    agent.mkdir(parents=True)
    remote = {"worker": "sebpc", "job": "7", "dummy": "a" * 64}
    (agent / "remote.json").write_text(json.dumps(remote))
    remote_agent.keep_record("kept", remote)
    bashrc = root / "bashrc"
    bashrc.write_text("kept\n")
    (agent / "remote-down.tmp").symlink_to(bashrc)
    (agent / "remote-down").symlink_to(bashrc)
    (agent / "remote-down").unlink()
    remote_agent.keep("kept", root / "kept", workers)
    found = remote_agent.load_remote(remote_agent.record_path("kept"))
    check("a pass on a remote agent keeps its record", found == remote, found)
    found = bashrc.read_text(), (agent / "remote-down").is_symlink()
    check("and writes no host file through a link at a temporary name", found == ("kept\n", False), found)
    remote_agent.run_folder("kept").mkdir(parents=True)
    (remote_agent.run_folder("kept") / "ended").write_text('{"reason": "verdict"}')
    (agent / "remote-ended").write_text("{}\n")
    remote_agent.keep("kept", root / "kept", workers)
    found = remote_agent.record_path("kept").exists()
    check("the end of its job drops it", not found, found)
    remote_agent.keep_record("kept", remote)
    (remote_agent.DUMMIES / remote["dummy"]).write_text("kept\n")
    runs.clear()
    remote_agent.drop_orphan("kept", remote)
    left = [path.name for path in (root / "record").iterdir() if path.is_file()]
    stops = [
        ["systemctl", "--user", "stop", "rr-tunnel-kept.scope"],
        ["podman", "rm", "-f", "rrs-kept"],
    ]
    check("drop_orphan stops the tunnel and the stand-in", runs == stops, runs)
    check("and drops the dummy login and the record", left == [], left)


def check_conflict(root):
    """What the stand-in says when the work tree cannot come back goes to remote-conflict as one
    line of printable characters: it prints names the bundle chose (security check 11-03, G1)."""
    remote_agent.STATE = remote_agent.DUMMIES = root / "conflict-state"
    agent = root / "c" / ".agent"
    agent.mkdir(parents=True)
    (agent / "remote-ended").write_text("{}\n")
    (agent / "remote.json").write_text(json.dumps({"worker": "sebpc", "job": "1"}))
    remote_agent.keep_record("c", {"worker": "sebpc", "job": "1"})
    said = b"x\nbash ~/src/odoo/b/odoo/p.sh #\x1b[2K changed here"
    kept = remote_agent.in_standin, remote_agent.subprocess.run
    remote_agent.in_standin = lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, b"", said)
    remote_agent.subprocess.run = lambda *args, **kwargs: subprocess.CompletedProcess(args, 0)
    try:
        remote_agent.finish("c", root / "c", {"reason": "verdict"})
    finally:
        remote_agent.in_standin, remote_agent.subprocess.run = kept
    found = (agent / "remote-conflict").read_text()
    check("remote-conflict is one printable line", found.count("\n") == 1 and found[:-1].isprintable(), found)


def check_ended(root):
    """An exec in the stand-in writes its run folder: the daemon reads there no link, no FIFO,
    nothing over MAX_ENDED, and its end removes only the regular files."""
    remote_agent.STATE = root / "state"
    run = remote_agent.run_folder("e")
    run.mkdir(parents=True)
    host_file = root / "host.json"
    host_file.write_text('{"reason": "read from a host file"}')
    (run / "ended").symlink_to(host_file)
    found = remote_agent.read_ended("e")
    check("an ended that links to a host file is not read", found is None, found)
    finished = []
    kept = {name: getattr(remote_agent, name) for name in ("read_remote", "finish", "is_tunnel_up")}
    remote_agent.read_remote = lambda folder: {"worker": "w", "job": "1"}
    remote_agent.finish = lambda *args: finished.append(args) or ("ended", False)
    remote_agent.is_tunnel_up = lambda bundle: True
    remote_agent.keep("e", root / "e", None)
    check("and the agent does not end on it", finished == [], finished)
    (run / "ended").unlink()
    os.mkfifo(run / "ended")
    started = time.monotonic()
    found = remote_agent.read_ended("e")
    spent = time.monotonic() - started
    check("an ended that is a FIFO gives None at once", found is None and spent < 1, (found, spent))
    (run / "ended").unlink()
    (run / "ended").write_text(json.dumps({"reason": "x" * remote_agent.MAX_ENDED}))
    found = remote_agent.read_ended("e")
    check("an ended over 64 KB is not read", found is None, found)
    (run / "ended").write_text('{"reason": "verdict", "exit": 0}\n')
    found = remote_agent.read_ended("e")
    check("a plain ended is read", found == {"reason": "verdict", "exit": 0}, found)
    remote_agent.keep("e", root / "e", None)
    check("and the agent ends on it", finished == [("e", root / "e", found)], finished)
    vars(remote_agent).update(kept)
    os.mkfifo(run / "fifo")
    (run / "link").symlink_to(host_file)
    (run / "attach.sock").write_text("")
    agent = root / "e" / ".agent"
    agent.mkdir(parents=True)
    (agent / "remote.json").write_text(json.dumps({"worker": "sebpc", "job": "1"}))
    remote_agent.keep_record("e", {"worker": "sebpc", "job": "1"})
    remote_agent.finish("e", root / "e", found)
    left = sorted(path.name for path in run.iterdir())
    check("its end removes only the regular files", left == ["attach.sock", "fifo", "link"], left)
    check("and no file a link names", host_file.exists())


def main():
    check_mounts()
    with tempfile.TemporaryDirectory() as folder:
        root = pathlib.Path(folder)
        check_config(root)
        check_env(root)
        check_remote(root)
        check_standin(root)
        check_start(root)
        check_binary(root)
        check_type(root)
        check_record(root)
        check_ended(root)
        check_conflict(root)
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    os.environ.setdefault("REMOTE_RUNS", "/nonexistent")
    main()
