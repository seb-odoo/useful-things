"""The agent of a bundle on a remote worker (remote-runs README, "Agents"; README.md, "Remote
agents"): its start from the queue, its tunnel kept up, its end, and its move back here.

What the agent's container reaches here goes through a stand-in container of the bundle,
`rrs-BUNDLE`, which runs `standin.py` of remote-runs: the gateways and the daemon name it as the
bundle, by its label `remote-runs.folder`. The worker gets no credential: a dummy login, which
claude-switch serves with the main one while the agent lives.
"""

import hashlib
import importlib.util
import io
import json
import math
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "devconfig", HERE.parent / "devcontainer" / "config.py"
)
devconfig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(devconfig)

HOME = pathlib.Path.home()
STATE = pathlib.Path("~/.local/state/bundle-ctl").expanduser()
REMOTE_RUNS = pathlib.Path(os.environ.get("REMOTE_RUNS") or HOME / "repo" / "remote-runs")
STANDIN_IMAGE = os.environ.get("BUNDLE_CTL_STANDIN_IMAGE", "localhost/remote-runs-runner:current")
DUMMIES = pathlib.Path("~/.local/state/claude-switch/dummies").expanduser()
# Where the job unpacks home/bundle-ctl/client of the config part: remote-runs sets HOME=/job/home.
JOB_CLIENT = "/job/home/bundle-ctl/client"
REPOS = ("odoo", "enterprise", "design-themes", "upgrade", "upgrade-util")
PAIR_RETRY = 30
STOP_WAIT = 660
STARTING = 1800
STOP_AGAIN = 300
MAX_LOSSES = 3
RENEW_BEFORE = 240
RENEW_EVERY = 300
REMOTE_FORMS = {
    "worker": re.compile(r"[a-z][a-z0-9-]{0,39}"),
    "job": re.compile(r"[0-9]{1,12}"),
    "dummy": re.compile(r"[0-9a-f]{64}"),
    "session": re.compile(r"(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?"),
}
REPOS = (
    "odoo",
    "enterprise",
    "design-themes",
    "documentation",
    "upgrade",
    "upgrade-util",
    "owl",
    "sfu",
)
finishing = {}
renewing = threading.Lock()
renewed = [0]
# Each mount of a bundle (devcontainer/base.jsonc) for a remote agent, its way there: a mount
# added to the bundles without a line here fails test_remote_agent.py.
MOUNTS = {
    "/workspace/.agent": ("job", "its own, the laptop's stays with the laptop"),
    "@REPO_ROOT@/odoo/.git": ("standin", "the agent's commits come back here, git-gate reads them"),
    "@REPO_ROOT@/enterprise/.git": ("standin", "same"),
    "@REPO_ROOT@/design-themes/.git": ("standin", "same"),
    "@REPO_ROOT@/documentation/.git": ("standin", "same"),
    "@REPO_ROOT@/upgrade/.git": ("standin", "same"),
    "@REPO_ROOT@/upgrade-util/.git": ("standin", "same"),
    "@REPO_ROOT@/owl/.git": ("standin", "same"),
    "@REPO_ROOT@/sfu/.git": ("standin", "same"),
    **{
        f"@REPO_ROOT@/{repo}/.git/{name}": ("standin", "read-only over the .git above")
        for repo in REPOS
        for name in ("config", "hooks")
    },
    **{f"@REPO_ROOT@/{repo}/.git/worktrees": ("standin", "same") for repo in REPOS},
    **{
        f"@REPO_ROOT@/{repo}/.git/worktrees/${{localWorkspaceFolderBasename}}": (
            "standin",
            "the bundle's own worktree, writable over it",
        )
        for repo in REPOS
    },
    "@WORKTREE_ROOT@/.claude": ("send", "the project settings of the bundles"),
    "/home/vscode/.claude-project": ("send", "the shared project config"),
    "@WORKTREE_ROOT@/.vscode": ("drop", "VS Code only"),
    "@USEFUL_THINGS@/odools.toml": ("drop", "the Odoo language server, off"),
    "/opt/node_modules": ("drop", "lint tooling: a remote agent runs no lint of the web tooling"),
    "/home/vscode/.claude": ("job", "its own claude folder, with what is sent"),
    "/home/vscode/.claude/CLAUDE.md": ("send", "rules"),
    "/home/vscode/.claude/agents": ("send", "agent types"),
    "/home/vscode/.claude/bin": ("send", "the tools of the skills"),
    "/home/vscode/.claude/commands": ("send", "commands"),
    "/home/vscode/.claude/external": ("send", "the Odoo CLAUDE.md the project links"),
    "/home/vscode/.claude/handoff": ("drop", "the proposals of the other bundles"),
    "/home/vscode/handoff": ("drop", "same"),
    "/home/vscode/handoff/${localWorkspaceFolderBasename}": ("job", "mirrored back"),
    "/home/vscode/.claude/hooks": ("send", "hooks"),
    "/home/vscode/.claude/output-styles": ("send", "output styles"),
    "/home/vscode/.claude/plugins": ("send", "plugins"),
    "/home/vscode/.claude/projects/@HOST_PROJECT@/memory": ("send", "the host memory, read-only"),
    "/home/vscode/.claude/projects/-workspace": ("send", "the memory of the bundles, the session"),
    "/home/vscode/.claude/projects/-workspace/memory": (
        "send",
        "the memory of the bundles, a copy of its own on an outside PR",
    ),
    "/home/vscode/.claude/plans": ("job", "its own: no plan of the laptop goes to a worker"),
    "/home/vscode/.claude/agent-start.lock": ("drop", "the lock of the agent starts of the laptop"),
    "/home/vscode/.claude/review-corpus": ("send", "the review skill reads it"),
    "/home/vscode/.claude/routines": ("send", "routines"),
    "/home/vscode/.claude/rules": ("send", "rules"),
    "/home/vscode/.claude/skills": ("send", "skills"),
    "/home/vscode/.claude/workflows": ("send", "workflows"),
    "/home/vscode/.claude/settings.json": ("send", "the bundle's settings"),
    "/home/vscode/.claude/.claude.json": (
        "send",
        "by an allow list of keys: MCP entries hold headers",
    ),
    "/home/vscode/.claude/daemon.json": ("drop", "claude's own daemon, made again"),
    "/home/vscode/.claude/policy-limits.json": ("send", "the limits of the account"),
    "/home/vscode/.claude/remote-settings-helper-consent": ("drop", "made again"),
    "/home/vscode/.claude/remote-settings.json": ("drop", "made again"),
    "/home/vscode/.claude/session-env": ("job", "made by claude"),
    "/home/vscode/.claude/sessions": ("job", "mirrored back"),
    "/home/vscode/.claude/shell-snapshots": ("job", "made by claude"),
    "/home/vscode/.claude/backups": ("job", "made by claude"),
    "/home/vscode/.claude/cache": ("job", "made by claude"),
    "/home/vscode/.claude/feedback": ("job", "made by claude"),
    "/home/vscode/.claude/file-history": ("job", "mirrored back"),
    "/home/vscode/.claude/ide": ("drop", "VS Code only"),
    "/home/vscode/.claude/dev-mods": ("drop", "mods of the Claude tab"),
    "/home/vscode/.vscode-server/data/User/globalStorage/anthropic.claude-code": (
        "drop",
        "VS Code",
    ),
    "/home/vscode/.config/Code/User": ("drop", "VS Code"),
    "/home/vscode/.config/gh": ("send", "the fake GitHub token of the bundle"),
    "/home/vscode/.ssh": ("send", "the fake SSH key of the bundle"),
    "@VENV_ROOT@": ("image", "the venvs, in the runner image"),
    "/home/vscode/.local/share/postgresql": ("job", "its own postgres"),
    "/var/run/postgresql": ("job", "its own postgres"),
    "/home/vscode/.local/share/Odoo": ("job", "its filestores"),
    "@REPO_ROOT@/TestWarden": ("image", "the engine, in the runner image"),
    "@REPO_ROOT@/TestWarden/engine/.cache": ("job", "a cache"),
    "/home/vscode/.cache/warden": ("image", "chrome-headless-shell, in the runner image"),
    "@REPO_ROOT@/DiscussModelParser": ("drop", "models.d.ts, for the editor"),
    "@REPO_ROOT@/runbot-fix-agents": ("send", "the skills of the runbot fixes"),
    "/usr/local/bin/gh": ("send", "gh, to the GitHub gateway"),
    "/home/vscode/.gitconfig.local": ("send", "the git identity"),
    "/home/vscode/.vscode-server/extensions": ("drop", "claude goes as its own part"),
    "@USEFUL_THINGS@/container-rw": ("image", "the shell helpers, in the runner image"),
    "/home/vscode/.claude-autoopen": ("drop", "VS Code"),
    "/home/vscode/.install-claude-autoopen.sh": ("drop", "VS Code"),
    "/run/bundle-ctl": ("standin", "bctl, through the tunnel"),
    "@USEFUL_THINGS@/bundle-ctl/client": ("send", "bctl and the run hook"),
}
CLAUDE_JSON_KEYS = ("hasCompletedOnboarding", "projects", "theme", "lastOnboardingVersion")
PROJECT_KEYS = (
    "allowedTools",
    "hasTrustDialogAccepted",
    "hasCompletedProjectOnboarding",
    "projectOnboardingSeenCount",
)


def get_values():
    return devconfig.load()


def get_paths(bundle_folder):
    """The host paths of a bundle's mounts, its placeholders filled."""
    values = get_values()
    claude_config = pathlib.Path(values["CACHE_ROOT"]) / "devcontainer" / "claude-config"
    return values, claude_config / bundle_folder.name


def get_memory(own):
    """The memory a bundle reads: a copy of its own on an outside PR (scripts/outside.py)."""
    if (own / "outside").is_file():
        return own / "memory"
    return HOME / ".claude" / "projects" / "-workspace" / "memory"


def get_agent(bundle_folder):
    return bundle_folder / ".agent"


def read_remote(bundle_folder):
    """The worker and the job of the bundle's agent while it is remote, else None. The bundle can
    write this file: the daemon names files, units and ssh words after it, so each value must
    have its form, and a file with any other value counts as no remote agent.
    """
    try:
        remote = json.loads((get_agent(bundle_folder) / "remote.json").read_text())
    except (OSError, ValueError):
        return None
    if type(remote) is not dict or not {"worker", "job"} <= remote.keys():
        return None
    for key, value in remote.items():
        form = REMOTE_FORMS.get(key)
        if form is None and key in ("started", "tunnel"):
            if type(value) not in (int, float) or not math.isfinite(value):
                return None
        elif form is None or type(value) is not str or not form.fullmatch(value):
            return None
    return remote


def is_remote(bundle_folder):
    """Whether the bundle's agent runs on a worker, or is starting there."""
    starting = get_mtime(get_agent(bundle_folder) / "remote-starting")
    return read_remote(bundle_folder) is not None or time.time() - starting < STARTING


def get_mtime(path):
    try:
        return pathlib.Path(path).stat().st_mtime
    except OSError:
        return 0


def run_folder(bundle):
    """The stand-in's /run/standin, a folder here: its sockets, and how the job ended."""
    return STATE / "standin" / bundle


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def build_config(bundle_folder):
    """The config part: what MOUNTS sends, at the paths the job unpacks (home/, workspace/)."""
    values, own = get_paths(bundle_folder)
    claude = HOME / ".claude"
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:

        def add(source, target):
            source = pathlib.Path(source)
            if source.exists():
                tar.add(source, arcname=target, filter=keep_regular)

        for name in (
            "CLAUDE.md",
            "agents",
            "bin",
            "commands",
            "external",
            "hooks",
            "output-styles",
            "plugins",
            "review-corpus",
            "routines",
            "rules",
            "skills",
            "workflows",
        ):
            add(claude / name, f"home/.claude/{name}")
        host_project = values.get("HOST_PROJECT") or "-home-seb-repo"
        add(
            claude / "projects" / host_project / "memory",
            f"home/.claude/projects/{host_project}/memory",
        )
        add(get_memory(own), "home/.claude/projects/-workspace/memory")
        add(own / "settings.json", "home/.claude/settings.json")
        add(own / "policy-limits.json", "home/.claude/policy-limits.json")
        project = own / "project"
        shared = pathlib.Path(values["SHARED_CLAUDE"])
        for path in sorted(project.iterdir()) if project.is_dir() else ():
            source = shared / path.name if path.is_symlink() else path
            if not source.is_file():
                continue
            data = source.read_bytes()
            if path.name == "settings.json":
                data = data.replace(str(HERE / "client").encode(), JOB_CLIENT.encode())
            info = tarfile.TarInfo(f"workspace/.claude/{path.name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add(own / "gh", "home/.config/gh")
        add(own / "ssh", "home/.ssh")
        add(HOME / ".gitconfig", "home/.gitconfig")
        add(HOME / ".gitconfig.local", "home/.gitconfig.local")
        add(HOME / ".local" / "bin" / "gh", "home/.local/bin/gh")
        repo_root = pathlib.Path(values["REPO_ROOT"])
        add(repo_root / "git-gate" / "client" / "git-gate-ssh", "home/.git-gate/git-gate-ssh")
        add(repo_root / "runbot-fix-agents", "home/runbot-fix-agents")
        add(HERE / "client", "home/bundle-ctl/client")
        try:
            whole = json.loads((own / ".claude.json").read_text())
        except (OSError, ValueError):
            whole = {}
        kept = {key: whole[key] for key in CLAUDE_JSON_KEYS if key in whole}
        if isinstance(kept.get("projects"), dict):
            kept["projects"] = {
                path: {key: entry[key] for key in PROJECT_KEYS if key in entry}
                for path, entry in kept["projects"].items()
                if isinstance(entry, dict)
            }
        kept = json.dumps(kept).encode()
        info = tarfile.TarInfo("home/.claude/.claude.json")
        info.size = len(kept)
        tar.addfile(info, io.BytesIO(kept))
    return out.getvalue()


def keep_regular(info):
    """Regular files, folders and links inside the tree only: no device, no link out."""
    if info.isfile() or info.isdir():
        return info
    if info.issym() and not info.linkname.startswith("/") and ".." not in info.linkname:
        return info
    return None


def find_claude(bundle_folder):
    """The claude of the bundle's extension store, the newest version."""
    values, _own = get_paths(bundle_folder)
    store = pathlib.Path(values["CACHE_ROOT"]) / "devcontainer" / "vscode-extensions"
    found = sorted(
        store.glob(f"{bundle_folder.name}/anthropic.claude-code-*/resources/native-binary/claude"),
        key=lambda path: path.stat().st_mtime,
    )
    if not found:
        found = sorted(
            store.glob("*/anthropic.claude-code-*/resources/native-binary/claude"),
            key=lambda path: path.stat().st_mtime,
        )
    if not found:
        raise RuntimeError("no claude in the extension stores")
    return found[-1]


def ssh(workers, worker, verb, bundle, *args, data=b"", timeout=600):
    """One verb of the worker, the answer line as JSON."""
    done = subprocess.run(
        workers.ssh(worker, verb, bundle, *args),
        input=data,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    try:
        answer = json.loads(done.stdout.splitlines()[0]) if done.stdout else {}
    except ValueError:
        answer = {}
    if not answer or "error" in answer:
        raise RuntimeError(f"{verb}: {answer.get('error') or done.stderr.decode()[-300:]}")
    return answer


def standin_name(bundle):
    return f"rrs-{bundle}"


def start_standin(bundle, bundle_folder, worker, session):
    """The stand-in: the bundle's mounts the tunnel serves, on devnet, as the bundle. Started in
    a scope of its own, as podman leaves its conmon in the cgroup of the caller, where a stop of
    bundle-ctl waited 90 s for it.
    """
    values, own = get_paths(bundle_folder)
    repo_root = pathlib.Path(values["REPO_ROOT"])
    state_root = pathlib.Path(values.get("STATE_ROOT") or HOME / ".local" / "state")
    mounts = [
        (bundle_folder, "/workspace", "rw"),
        (HOME / ".claude" / "projects" / "-workspace", "/mirror/projects", "rw"),
        (get_memory(own), "/mirror/projects/memory", "rw"),
        (own / "sessions", "/mirror/sessions", "rw"),
        (own / "file-history", "/mirror/file-history", "rw"),
        (HOME / ".claude" / "handoff" / bundle, "/mirror/handoff", "rw"),
        (state_root / "bundle-ctl" / "sock", "/run/bundle-ctl", "ro"),
        (state_root / "git-gate" / "sock", "/run/git-gate", "ro"),
        (REMOTE_RUNS, "/opt/remote-runs", "ro"),
        (run_folder(bundle), "/run/standin", "rw"),
    ]
    admin = HERE.parent / "devcontainer" / "worktree-admin.py"
    subprocess.run([sys.executable, str(admin), str(bundle_folder)], check=True)
    for repo in REPOS:
        git_dir = repo_root / repo / ".git"
        if git_dir.is_dir():
            mounts.append((git_dir, str(git_dir), "rw"))
            names = ("config", "hooks", "worktrees")
            mounts += [(git_dir / name, str(git_dir / name), "ro") for name in names]
            own = git_dir / "worktrees" / bundle_folder.name
            mounts.append((own, str(own), "rw"))
    argv = [
        "podman",
        "run",
        "-d",
        "--rm",
        f"--name={standin_name(bundle)}",
        "--userns=keep-id",
        "--network=devnet",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory=512m",
        "--pids-limit=512",
        "--workdir=/",
        f"--label=remote-runs.folder={bundle_folder}",
        f"--label=remote-runs.worker={worker}",
        "-e",
        f"STANDIN_BUNDLE={bundle}",
        "-e",
        f"STANDIN_SESSION={session}",
    ]
    for source, target, mode in mounts:
        if not pathlib.Path(source).exists():
            pathlib.Path(source).mkdir(parents=True)
        argv += ["-v", f"{source}:{target}:{mode}"]
    subprocess.run(["podman", "rm", "-f", standin_name(bundle)], capture_output=True, check=False)
    scope = ["systemd-run", "--user", "--scope", "--collect", "--quiet"]
    subprocess.run(
        [*scope, *argv, STANDIN_IMAGE, "sleep", "infinity"], capture_output=True, check=True
    )


def in_standin(bundle, verb, data=b"", check=True):
    command = [
        "podman",
        "exec",
        "-i",
        standin_name(bundle),
        "python3",
        "-I",
        "/opt/remote-runs/standin/standin.py",
        verb,
    ]
    return subprocess.run(command, input=data, capture_output=True, check=check)


def start(log, bundle, bundle_folder, worker, workers, base, session, resume):
    """Start the agent of the bundle on WORKER, its task in `.agent/task.md`. Raise on a failure
    before the job started: the queue then starts it here. After a job that was lost, the agent
    goes on from the worker's tree (`.agent/remote-lost`), which the laptop never got back.
    """
    agent = get_agent(bundle_folder)
    log.write(f"starting the agent of {bundle} on {worker}\n")
    (agent / "remote-starting").touch()
    dummy = f"sk-ant-oat01-rr-{secrets.token_urlsafe(36)}"
    named = DUMMIES / hashlib.sha256(dummy.encode()).hexdigest()
    answer = {}
    try:
        start_standin(bundle, bundle_folder, worker, session)
        haves = {
            repo: ssh(workers, worker, "have", bundle, repo)
            for repo in REPOS
            if (bundle_folder / repo / ".git").exists()
        }
        order = {"repos": haves, "base": base, "session": session, "resume": resume}
        packed = in_standin(bundle, "pack", json.dumps(order).encode(), check=False)
        if packed.returncode:
            raise RuntimeError(packed.stderr.decode().strip()[-500:])
        files = {}
        with tarfile.open(fileobj=io.BytesIO(packed.stdout)) as tar:
            for member in tar:
                files[member.name] = tar.extractfile(member).read()
        repos = json.loads(files.pop("spec.json"))
        for name, data in files.items():
            ssh(workers, worker, "agent-put", bundle, name, data=data)
        parts = {
            "config": build_config(bundle_folder),
            "claude": find_claude(bundle_folder),
            "ca": HOME / ".config" / "gateway-ca" / "ca-certificates.crt",
        }
        shas = {}
        for name, value in parts.items():
            data = value if isinstance(value, bytes) else pathlib.Path(value).read_bytes()
            shas[name] = (hashlib.sha256(data).hexdigest(), data)
        spec = {
            "session": session,
            "resume": resume,
            "keep": (agent / "remote-lost").exists(),
            "task": read(agent / "task.md")[:65536],
            "model": read(agent / "model") or "opus",
            "effort": read(agent / "effort") or "medium",
            "mode": read(agent / "mode") or "auto",
            "config": shas["config"][0],
            "claude": shas["claude"][0],
            "ca": shas["ca"][0],
            "dummy": dummy,
            "name": bundle,
            "repos": repos,
            "in": sorted(files),
            "env": get_env(),
        }
        DUMMIES.mkdir(parents=True, exist_ok=True)
        named.write_text(f"{bundle}\n")
        answer = ssh(workers, worker, "agent", bundle, data=json.dumps(spec).encode())
        for sha in answer.get("missing", []):
            name = next(name for name, (each, _) in shas.items() if each == sha)
            log.write(f"sending the {name} part to {worker}\n")
            ssh(workers, worker, "part", bundle, sha, data=shas[name][1], timeout=3600)
        if "missing" in answer:
            answer = ssh(workers, worker, "agent", bundle, data=json.dumps(spec).encode())
        if "job" not in answer:
            raise RuntimeError(answer.get("refused") or answer)
        remote = {
            "worker": worker,
            "job": answer["job"],
            "started": time.time(),
            "dummy": named.name,
            "session": session,
        }
        (run_folder(bundle) / "ended").unlink(missing_ok=True)
        write(agent / "remote.json", json.dumps(remote) + "\n")
    except Exception:
        if "job" in answer:
            subprocess.run(
                workers.ssh(worker, "cancel", bundle, answer["job"]),
                capture_output=True,
                check=False,
                timeout=60,
            )
        named.unlink(missing_ok=True)
        subprocess.run(
            ["podman", "rm", "-f", standin_name(bundle)], capture_output=True, check=False
        )
        raise
    finally:
        (agent / "remote-starting").unlink(missing_ok=True)
    write(agent / "where", f"worker:{worker}\n")
    (agent / "run.lock").mkdir(exist_ok=True)
    open_tunnel(bundle, remote, workers)
    log.write(f"the agent of {bundle} runs on {worker}, job {answer['job']}\n")


def get_env():
    """The environment of a bundle the job takes: the containerEnv of the generated config."""
    try:
        root = pathlib.Path(get_values()["WORKTREE_ROOT"])
        lines = (root / ".devcontainer" / "devcontainer.json").read_text().splitlines()
        config = json.loads("\n".join(line for line in lines if line[:2] != "//"))
    except (OSError, ValueError, KeyError):
        return {}
    kept = {}
    for name, value in (config.get("containerEnv") or {}).items():
        fixed = (
            "PROXY",
            "CLAUDE_CONFIG_DIR",
            "HOME",
            "PG",
            "SSL_CERT",
            "REQUESTS_CA",
            "EGRESS",
            "GIT_SSH",
            "AGENT_",
            "LD_",
            "PYTHONPYCACHE",
        )
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name) and not any(part in name for part in fixed):
            kept[name] = str(value)[:4096]
    if "BUNDLE_CTL_CLIENT" in kept:
        kept["BUNDLE_CTL_CLIENT"] = JOB_CLIENT
    return dict(list(kept.items())[:64])


def tunnel_unit(bundle):
    return f"rr-tunnel-{bundle}.scope"


def open_tunnel(bundle, remote, workers):
    """`ssh ... tunnel` and the stand-in's end, joined by pair.py in a scope of their own: a
    restart of bundle-ctl cuts no tunnel.
    """
    ssh_argv = workers.ssh(remote["worker"], "tunnel", bundle, remote["job"])
    standin = [
        "podman",
        "exec",
        "-i",
        standin_name(bundle),
        "python3",
        "-I",
        "/opt/remote-runs/standin/standin.py",
        "tunnel",
    ]
    subprocess.run(
        ["systemctl", "--user", "reset-failed", tunnel_unit(bundle)],
        capture_output=True,
        check=False,
    )
    subprocess.Popen(
        [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            f"--unit={tunnel_unit(bundle)}",
            sys.executable,
            str(REMOTE_RUNS / "standin" / "pair.py"),
            *ssh_argv,
            "--",
            *standin,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def is_tunnel_up(bundle):
    done = subprocess.run(
        ["systemctl", "--user", "is-active", tunnel_unit(bundle)],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() in ("active", "activating")


def read_ended(bundle):
    try:
        return json.loads((run_folder(bundle) / "ended").read_text())
    except (OSError, ValueError):
        return None


def is_standin_up(bundle):
    done = subprocess.run(
        ["podman", "container", "exists", standin_name(bundle)], capture_output=True, check=False
    )
    return done.returncode == 0


def keep(bundle, bundle_folder, workers):
    """One pass of the daemon on a remote agent: its tunnel (and its stand-in) up, or its end
    once its job ended. Returns (a line for the journal or None, whether its job was lost).
    """
    remote = read_remote(bundle_folder)
    if remote is None:
        return None, False
    agent = get_agent(bundle_folder)
    ended = read_ended(bundle)
    if ended is not None:
        return finish(bundle, bundle_folder, ended)
    if is_tunnel_up(bundle) or time.time() - remote.get("tunnel", 0) < PAIR_RETRY:
        return None, False
    if not workers.is_up(remote["worker"]):
        workers.ask(remote["worker"])
        if not (agent / "remote-down").exists():
            write(agent / "remote-down", time.strftime("%H:%M") + "\n")
        return None, False
    remote["tunnel"] = time.time()
    write(agent / "remote.json", json.dumps(remote) + "\n")
    if not is_standin_up(bundle):
        start_standin(bundle, bundle_folder, remote["worker"], remote.get("session", ""))
    open_tunnel(bundle, remote, workers)
    (agent / "remote-down").unlink(missing_ok=True)
    return f"{bundle}: its tunnel to {remote['worker']} opened again", False


def finish(bundle, bundle_folder, end):
    """The job ended. When it sent its end (`remote-ended`), its work tree is written here; when
    it was lost, its tree stays on the worker, where its next start goes on from it. Once per
    bundle, whoever calls first. Returns (a line for the journal, whether the job was lost).
    """
    with finishing.setdefault(bundle, threading.Lock()):
        remote = read_remote(bundle_folder)
        if remote is None:
            return None, False
        agent = get_agent(bundle_folder)
        sent_end = (agent / "remote-ended").exists()
        line = f"{bundle}: its agent on {remote['worker']} ended ({end.get('reason') or end.get('exit')})"
        lost = not sent_end
        if sent_end:
            back = in_standin(bundle, "back", check=False)
            if back.returncode == 0:
                line += ", its work tree is back"
                (agent / "remote-lost").unlink(missing_ok=True)
                shutil.rmtree(agent / "remote-back", ignore_errors=True)
            else:
                why = back.stderr.decode().strip()[-300:]
                line += f", its work tree stays in .agent/remote-back: {why}"
                write(agent / "remote-conflict", why + "\n")
                write(agent / "where", "local\n")
        else:
            losses = int(read(agent / "remote-lost") or 0) + 1
            transcript = (
                HOME / ".claude" / "projects" / "-workspace" / f"{remote.get('session')}.jsonl"
            )
            if transcript.exists() and losses < MAX_LOSSES:
                line += ", lost: its next start goes on from its tree on the worker"
                write(agent / "remote-lost", f"{losses}\n")
            else:
                line += ", lost again or before claude started: its next start is here"
                (agent / "remote-lost").unlink(missing_ok=True)
                write(agent / "where", "local\n")
        subprocess.run(
            ["systemctl", "--user", "stop", tunnel_unit(bundle)], capture_output=True, check=False
        )
        subprocess.run(
            ["podman", "rm", "-f", standin_name(bundle)], capture_output=True, check=False
        )
        if remote.get("dummy"):
            (DUMMIES / remote["dummy"]).unlink(missing_ok=True)
        moved = (agent / "remote-moving").exists()
        for name in (
            "remote.json",
            "remote-ended",
            "remote-moving",
            "remote-sent.json",
            "remote-down",
            "remote-ending",
        ):
            (agent / name).unlink(missing_ok=True)
        for path in run_folder(bundle).glob("*"):
            if path.is_file() and not path.name.endswith(".sock"):
                path.unlink(missing_ok=True)
        if moved and sent_end:
            write(agent / "where", "local\n")
        return line, lost


def end(bundle, bundle_folder):
    """An agent that gave its verdict: claude stops and its work tree comes back, as for a move
    back, but its next task goes to the same worker. Asked once; keep() ends the rest.
    """
    agent = get_agent(bundle_folder)
    if time.time() - get_mtime(agent / "remote-ending") < STOP_AGAIN:
        return None
    (agent / "remote-ending").touch()
    in_standin(bundle, "order", json.dumps({"op": "stop"}).encode() + b"\n", check=False)
    return f"{bundle}: its agent gave its verdict, it stops on its worker"


def move_back(log, bundle, bundle_folder, workers):
    """Bring the agent here: claude stops once idle or in a tool call, its work tree comes back,
    then the caller starts its terminal on the same session. Whatever the job does, it is
    cancelled after STOP_WAIT.
    """
    remote = read_remote(bundle_folder)
    if remote is None:
        raise RuntimeError(f"the agent of {bundle} is not remote")
    log.write(f"bringing the agent of {bundle} back from {remote['worker']}\n")
    (get_agent(bundle_folder) / "remote-moving").touch()
    in_standin(bundle, "order", json.dumps({"op": "stop"}).encode() + b"\n", check=False)
    deadline = time.time() + STOP_WAIT
    cancelled = False
    while True:
        ended = read_ended(bundle)
        if ended is not None:
            line, _lost = finish(bundle, bundle_folder, ended)
            return line or f"{bundle}: its agent ended meanwhile"
        if time.time() > deadline and not cancelled:
            cancelled = True
            try:
                ssh(workers, remote["worker"], "cancel", bundle, remote["job"], timeout=60)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                log.write(f"cancel on {remote['worker']}: {error}\n")
        if time.time() > deadline + 120:
            line, _lost = finish(bundle, bundle_folder, {"reason": "cancelled"})
            return line or f"{bundle}: its agent ended meanwhile"
        time.sleep(2)


def renew_login_soon(bundle_folder):
    """renew_login in a thread of its own, one at a time: the daemon's pass goes on."""
    if renewing.acquire(blocking=False):

        def run():
            try:
                if line := renew_login(bundle_folder):
                    print(line, flush=True)
            finally:
                renewing.release()

        threading.Thread(target=run, daemon=True).start()


def renew_login(bundle_folder):
    """While a remote agent or a bundle needs it, the main login renewed before it expires:
    claude-switch sends their requests with it, as both hold a dummy login, and at night no ops
    session may renew it. A one-shot claude in a container that shares the login, as the ops
    container does, renews it with their lock.
    """
    try:
        login = json.loads((HOME / ".claude" / ".credentials.json").read_text())["claudeAiOauth"]
        left = login["expiresAt"] / 1000 - time.time()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if left > RENEW_BEFORE or time.time() - renewed[0] < RENEW_EVERY:
        return None
    renewed[0] = time.time()
    command = [
        "podman",
        "run",
        "--rm",
        "--userns=keep-id",
        "--network=devnet",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "-e",
        "CLAUDE_CONFIG_DIR=/home/vscode/.claude",
        "-e",
        "HTTPS_PROXY=http://10.89.255.20:3128",
        "-v",
        f"{HOME / '.claude'}:/home/vscode/.claude",
        "-v",
        f"{find_claude(bundle_folder)}:/usr/local/bin/claude:ro",
        "-v",
        f"{HOME / '.config' / 'gateway-ca' / 'ca-certificates.crt'}:/etc/ssl/certs/ca-certificates.crt:ro",
        STANDIN_IMAGE,
        "claude",
        "-p",
        "--model",
        "haiku",
        "Answer ok.",
    ]
    done = subprocess.run(command, capture_output=True, check=False, timeout=180)
    return (
        f"the main login expired in {left:.0f} s, renewed for the remote agents ({done.returncode})"
    )


def read(path):
    try:
        return pathlib.Path(path).read_text().strip()
    except OSError:
        return ""


def write(path, text):
    temporary = pathlib.Path(f"{path}.tmp")
    temporary.write_text(text)
    temporary.replace(path)
