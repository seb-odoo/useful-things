"""A run of this bundle on a remote worker, as the run queue chose (README.md, "Remote runs").

The worktrees go as snapshots (snapshot.py), the DB the command names goes along when it exists
here, the output comes back as it is written, and a DB the command made or changed comes back
under its name. A Hoot engine run takes along the bundle's server it points at: its DB, its
addons path and its options, read in /proc, from which the worker starts that server in the job.
Anything that fails before the job started returns None: the caller runs the command here
instead, so the agent never depends on the worker.
"""

import io
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse

import snapshot

FRAME = struct.Struct(">cI")
MAX_FRAME = 1 << 20
REPOS = ("odoo", "enterprise", "design-themes", "upgrade", "upgrade-util")
SOCKET = pathlib.Path("/run/bundle-ctl/ctl.sock")
WORKSPACE = pathlib.Path("/workspace")
CACHE = pathlib.Path.home() / ".cache" / "remote-runs"
FILESTORE = pathlib.Path.home() / ".local" / "share" / "Odoo" / "filestore"
DB_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
CHANGES_DB = {"-i", "--init", "-u", "--update"}
MAX_WAIT = 600
NOT_STARTED = 125
ENGINE = re.compile(r"engine-linux-\w+|test-warden-engine")
ENGINE_SERVER = re.compile(r"http://(?:localhost|127\.0\.0\.1):([0-9]{1,5})(?:/\S*)?")
VENV_PYTHON = re.compile(r"/(odoo[0-9]{2})/bin/python[0-9.]*$")
VENV = re.compile(r"odoo[0-9]{2}")
SERVER_OPTIONS = {"dev", "load", "log-handler", "log-level", "server-wide-modules"}
SERVER_DROPPED = {
    "--db-filter", "--db_host", "--db_password", "--db_port", "--db_user", "--gevent-port",
    "--http-interface", "--http-port", "--limit-memory-hard", "--limit-memory-soft",
    "--limit-request", "--limit-time-cpu", "--limit-time-real", "--limit-time-real-cron",
    "--logfile", "--longpolling-port", "--max-cron-threads", "--no-database-list", "--pidfile",
    "--proxy-mode", "--with-demo", "--without-demo", "--workers", "--x-sendfile", "-p", "-r", "-w",
}


class Fallback(Exception):
    """The run cannot go to the worker: it runs here."""


def say(text):
    print(f"bctl run: {text}", file=sys.stderr, flush=True)


def socket_path():
    if SOCKET.exists():
        return SOCKET
    raise Fallback("no bundle-ctl socket")


def open_stream(worker, verb, *args):
    """A connection to the worker through the daemon, past its answer header."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(str(socket_path()))
    except OSError as error:
        raise Fallback(f"bundle-ctl: {error}") from error
    query = urllib.parse.urlencode([("worker", worker)] + [("arg", arg) for arg in args])
    connection.sendall(f"POST /worker/{verb}?{query} HTTP/1.0\r\n\r\n".encode())
    reader = connection.makefile("rb")
    status = reader.readline().split()
    while reader.readline() not in (b"\r\n", b"\n", b""):
        pass
    if status[1:2] != [b"200"]:
        try:
            error = json.loads(reader.read(65536)).get("error", "")
        except ValueError:
            error = ""
        raise Fallback(f"{verb}: {b' '.join(status[1:]).decode(errors='replace')} {error}".strip())
    return connection, reader


def call(worker, verb, *args, body=b""):
    """The answer of a verb that takes a body and answers one line of JSON."""
    connection, reader = open_stream(worker, verb, *args)
    try:
        if body:
            connection.sendall(body)
        connection.shutdown(socket.SHUT_WR)
        line = reader.readline()
    except OSError as error:
        raise Fallback(f"{verb}: {error}") from error
    finally:
        connection.close()
    try:
        answer = json.loads(line)
    except ValueError as error:
        raise Fallback(f"{verb}: no answer from the worker") from error
    if "error" in answer:
        detail = " / ".join(answer.get("detail") or [])
        raise Fallback(f"{verb}: {answer['error']}" + (f" ({detail})" if detail else ""))
    return answer


def read_frames(reader):
    while True:
        header = reader.read(FRAME.size)
        if len(header) < FRAME.size:
            return
        kind, size = FRAME.unpack(header)
        if size > MAX_FRAME:
            return
        payload = reader.read(size)
        if len(payload) < size:
            return
        yield kind.decode("ascii", "replace"), payload


def git(cwd, *args):
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                            check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def get_base(worktree, base):
    """The upstream commit the worktree starts from: its merge base with the base branch of the
    upstream remote, else its HEAD.
    """
    head = git(worktree, "rev-parse", "HEAD")
    for line in (git(worktree, "remote", "-v") or "").splitlines():
        name, url = line.split()[:2]
        if re.search(rf"[:/]odoo/{re.escape(worktree.name)}(\.git)?$", url):
            found = git(worktree, "merge-base", "HEAD", f"{name}/{base}")
            if found:
                return found
    return head


def get_database(argv):
    for index, word in enumerate(argv):
        if word in ("-d", "--database") and index + 1 < len(argv):
            return argv[index + 1]
        if word.startswith("--database="):
            return word.split("=", 1)[1]
        if word.startswith("-d") and len(word) > 2 and not word.startswith("--"):
            return word[2:]
    return None


def psql(*args, database="postgres"):
    result = subprocess.run(["psql", "-qAtX", "-d", database, *args], capture_output=True,
                            text=True, check=False)
    return result.stdout if result.returncode == 0 else None


def exists(database):
    return bool(psql("-c", f"SELECT 1 FROM pg_database WHERE datname = '{database}'"))


def pack_database(database):
    """The DB as `pg_dump -Fc` and its filestore without assets, in a tar."""
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as temp:
        dump = pathlib.Path(temp) / "db.dump"
        if subprocess.run(["pg_dump", "-Fc", "-f", str(dump), database], check=False).returncode:
            raise Fallback(f"pg_dump of {database} failed")
        files = psql("-c", "SELECT DISTINCT store_fname FROM ir_attachment WHERE store_fname IS"
                     " NOT NULL AND (url IS NULL OR url NOT LIKE '/web/assets/%')",
                     database=database) or ""
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            tar.add(dump, arcname="db.dump")
            store = FILESTORE / database
            for name in files.split():
                path = store / name
                if path.is_file() and not path.is_symlink():
                    tar.add(path, arcname=f"filestore/{name}")
    return buffer.getvalue()


def extract(data, target):
    """Regular files and folders only, each inside `target`."""
    target = pathlib.Path(target).resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        for member in tar:
            path = (target / member.name).resolve()
            if target not in path.parents and path != target:
                continue
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, open(path, "wb") as sink:
                    shutil.copyfileobj(source, sink)


def drop(database):
    subprocess.run(["dropdb", "--if-exists", "--force", database], capture_output=True, check=False)


def restore(database, folder):
    """The DB the job sent back, put here under its name once whole."""
    dump = folder / "db.dump"
    if not dump.is_file():
        say(f"{database} did not come back")
        return
    temporary = f"{database[:50]}-rr-restore"
    drop(temporary)
    if subprocess.run(["createdb", temporary], check=False).returncode or subprocess.run(
        ["pg_restore", "-d", temporary, "--no-owner", "-j", "2", str(dump)],
        capture_output=True, check=False,
    ).returncode > 1:
        say(f"{database}: the restore failed, the DB here is left as it was")
        drop(temporary)
        return
    drop(database)
    psql("-c", f'ALTER DATABASE "{temporary}" RENAME TO "{database}"')
    store = FILESTORE / database
    shutil.rmtree(store, ignore_errors=True)
    if (folder / "filestore.tar").is_file():
        store.mkdir(parents=True)
        extract((folder / "filestore.tar").read_bytes(), store)


def sync(worker, base):
    """Each worktree of the bundle on the worker, as it is now: repo to snapshot commit."""
    snaps = {}
    for repo in REPOS:
        worktree = WORKSPACE / repo
        if not (worktree / ".git").exists():
            continue
        have = call(worker, "have", repo)
        commit = get_base(worktree, base)
        cache = CACHE / repo
        cache.mkdir(parents=True, exist_ok=True)
        try:
            snap, data = snapshot.Snapshot(worktree, cache).make(commit, have)
        except snapshot.Refused as error:
            raise Fallback(f"{repo}: {error}") from error
        if data:
            answer = call(worker, "sync", repo, commit, body=data)
            if answer.get("snap") != snap:
                raise Fallback(f"{repo}: the worker got {answer.get('snap')}, not {snap}")
        snaps[repo] = snap
    if not snaps:
        raise Fallback("no worktree to send")
    return snaps


def get_databases(argv, cwd):
    """The DBs an odoo-bin run sends to the worker, and the ones it gets back."""
    if cwd != WORKSPACE and WORKSPACE not in cwd.parents:
        raise Fallback("not run from /workspace")
    database = get_database(argv)
    if database and not DB_NAME.fullmatch(database):
        raise Fallback(f"database name {database!r}")
    dbs_in, dbs_out = [], []
    if database:
        if exists(database):
            dbs_in.append(database)
            if CHANGES_DB.intersection(argv) or any(
                word.startswith(("--init=", "--update=")) for word in argv
            ):
                dbs_out.append(database)
        else:
            dbs_out.append(database)
    return dbs_in, dbs_out


def get_port(argv):
    for index, word in enumerate(argv):
        name, equal, value = word.partition("=")
        if name in ("--server", "-server"):
            url = value if equal else "".join(argv[index + 1 : index + 2])
            if match := ENGINE_SERVER.fullmatch(url):
                return int(match[1])
    raise Fallback("no --server http://localhost:PORT")


def find_server(port):
    """The /proc folder of the odoo-bin of this bundle that listens on PORT."""
    sockets = set()
    for table in ("tcp", "tcp6"):
        try:
            lines = (pathlib.Path("/proc/net") / table).read_text().splitlines()[1:]
        except OSError:
            continue
        for fields in map(str.split, lines):
            if fields[3] == "0A" and int(fields[1].rpartition(":")[2], 16) == port:
                sockets.add(f"socket:[{fields[9]}]")
    for proc in sorted(pathlib.Path("/proc").glob("[0-9]*"), key=lambda path: int(path.name)):
        try:
            if sockets.isdisjoint(os.readlink(fd) for fd in (proc / "fd").iterdir()):
                continue
            words = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(os.path.basename(word) == b"odoo-bin" for word in words):
            return proc
    raise Fallback(f"no odoo-bin of this bundle listens on port {port}")


def get_venv(python, environ):
    """The venv of the server's python: its path, else the VIRTUAL_ENV of its environment."""
    if python:
        program = python[0]
        if "/" not in program:
            program = shutil.which(program, path=environ.get("PATH", "")) or program
        if match := VENV_PYTHON.search(program):
            return match[1]
    name = pathlib.Path(environ.get("VIRTUAL_ENV", "")).name
    if VENV.fullmatch(name):
        return name
    raise Fallback("the server runs the python of no odooNN venv")


def get_addons(path, cwd):
    full = pathlib.Path(os.path.normpath(cwd / os.path.expanduser(path)))
    if WORKSPACE not in full.parents or full.relative_to(WORKSPACE).parts[0] not in REPOS:
        raise Fallback(f"the addons path {path} is not in a worktree of the bundle")
    return str(full.relative_to(WORKSPACE))


def read_server(port):
    """The venv and the server of a hoot job: the DB, the addons path and the options of the
    bundle's server on PORT. An option of the laptop (port, log file, DB access) is dropped, any
    other one runs the command here.
    """
    proc = find_server(port)
    try:
        words = (proc / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
        cwd = pathlib.Path(os.readlink(proc / "cwd"))
        environ = dict(
            line.partition("=")[::2]
            for line in (proc / "environ").read_bytes().decode(errors="replace").split("\0")
        )
    except (OSError, UnicodeDecodeError) as error:
        raise Fallback(f"the server on port {port}: {error}") from error
    programs = [index for index, word in enumerate(words) if os.path.basename(word) == "odoo-bin"]
    at = programs[0] if programs else 0
    if pathlib.Path(os.path.normpath(cwd / words[at])) != WORKSPACE / "odoo" / "odoo-bin":
        raise Fallback(f"the server runs {words[at]}, not {WORKSPACE}/odoo/odoo-bin")
    venv = get_venv(words[:at], environ)
    args = words[at + 1 :]
    args = args[1:] if args[:1] == ["server"] else args
    database, addons, options, index = None, [], [], 0
    while index < len(args):
        word = args[index]
        index += 1
        name, equal, value = word.partition("=")
        if not name.startswith("-"):
            raise Fallback(f"the server word {word!r}")
        if not name.startswith("--") and len(name) > 2:
            name, equal, value = name[:2], "=", word[2:]
        if not equal and index < len(args) and not args[index].startswith("-"):
            equal, value = "=", args[index]
            index += 1
        if name in ("-d", "--database"):
            database = value
        elif name == "--addons-path":
            addons = [get_addons(path, cwd) for path in value.split(",") if path]
        elif name.removeprefix("--") in SERVER_OPTIONS and equal:
            options.append(f"{name}={value}")
        elif name not in SERVER_DROPPED:
            raise Fallback(f"the server option {name}")
    if not database or not DB_NAME.fullmatch(database) or not exists(database):
        raise Fallback(f"the server on port {port} names no DB of this bundle")
    return venv, {"database": database, "addons_path": addons, "options": options}


def get_engine_run(argv, cwd):
    """The engine's command as the job runs it, the venv and the server of the job."""
    folder = os.environ.get("TESTWARDEN")
    program = pathlib.Path(os.path.normpath(cwd / argv[0]))
    movable = folder and "/" in argv[0] and pathlib.Path(folder) in program.parents
    if not movable or not ENGINE.fullmatch(program.name):
        raise Fallback(f"{argv[0]} is not an engine of TESTWARDEN")
    venv, server = read_server(get_port(argv))
    return [str(program), *argv[1:]], venv, server


def submit(worker, argv, kind, workers, base):
    cwd = pathlib.Path.cwd()
    venv = pathlib.Path(os.environ.get("VIRTUAL_ENV") or "odoo20").name
    server = None
    if kind == "hoot":
        argv, venv, server = get_engine_run(argv, cwd)
        cwd, dbs_in, dbs_out = WORKSPACE, [server["database"]], []
    else:
        dbs_in, dbs_out = get_databases(argv, cwd)
    snaps = sync(worker, base)
    for name in dbs_in:
        call(worker, "put", name, body=pack_database(name))
    spec = {
        "argv": argv,
        "cwd": str(cwd.relative_to(WORKSPACE)) if cwd != WORKSPACE else "",
        "kind": kind if kind in ("hoot", "odoo") else "other",
        "workers": workers,
        "snaps": snaps,
        "venv": venv,
        "dbs_in": dbs_in,
        "dbs_out": dbs_out,
        "max_wait": MAX_WAIT,
    }
    if server:
        spec["server"] = server
    answer = call(worker, "submit", body=json.dumps(spec).encode())
    if "refused" in answer:
        raise Fallback(f"refused: {answer['refused']}")
    return answer["job"], dbs_out


def follow(worker, job):
    """Print the job's output as it comes; its exit code, or None when the stream broke."""
    connection, reader = open_stream(worker, "follow", job)
    connection.shutdown(socket.SHUT_WR)
    told = None
    for kind, payload in read_frames(reader):
        if kind == "o":
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()
        elif kind == "e":
            sys.stderr.buffer.write(payload)
            sys.stderr.buffer.flush()
        elif kind == "s":
            state = json.loads(payload)
            text = (f"waits on {worker}, place {state['place']} ({state.get('blocker')})"
                    if state.get("place") else f"{state['state']} on {worker}")
            if text != told:
                told = text
                say(text)
        elif kind == "x":
            end = json.loads(payload)
            if end.get("reason"):
                say(f"on {worker}: {end['reason']}")
            connection.close()
            return end.get("exit")
    connection.close()
    return None


def get_back(worker, job, dbs_out):
    connection, reader = open_stream(worker, "get", job)
    connection.shutdown(socket.SHUT_WR)
    data = reader.read()
    connection.close()
    with tempfile.TemporaryDirectory() as temp:
        extract(data, temp)
        for name in dbs_out:
            restore(name, pathlib.Path(temp) / name)


def run(argv, worker, kind, workers, base):
    """The exit code of the command run on the worker, or None to run it here."""
    try:
        job, dbs_out = submit(worker, argv, kind, workers, base)
    except Fallback as error:
        say(f"runs here, not on {worker}: {error}")
        return None
    say(f"job {job} on {worker}")

    def cancel(signum, frame):
        try:
            call(worker, "cancel", job)
        except Fallback:
            pass
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    try:
        code = follow(worker, job)
    except Fallback as error:
        code, reason = None, str(error)
    else:
        reason = "the stream broke"
    if code is None:
        say(f"lost job {job} on {worker} ({reason}), runs here again")
        return None
    if code == NOT_STARTED:
        say(f"runs here, not on {worker}: job {job} did not start")
        return None
    if dbs_out:
        try:
            get_back(worker, job, dbs_out)
        except (Fallback, OSError, tarfile.TarError) as error:
            say(f"the DB did not come back: {error}")
    return code
