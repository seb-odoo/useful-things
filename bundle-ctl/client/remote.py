"""A run of this bundle on a remote worker, as the run queue chose (README.md, "Remote runs").

The worktrees go as snapshots (snapshot.py), the DB the command names goes along when it exists
here, the output comes back as it is written, and a DB the command made or changed comes back
under its name. Anything that fails before the job started returns None: the caller runs the
command here instead, so the agent never depends on the worker.
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
        raise Fallback(f"{verb}: {answer['error']}")
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


def restore(database, folder):
    """The DB the job sent back, put here under its name once whole."""
    dump = folder / "db.dump"
    if not dump.is_file():
        say(f"{database} did not come back")
        return
    temporary = f"{database[:50]}-rr-restore"
    subprocess.run(["dropdb", "--if-exists", "--force", temporary], check=False)
    if subprocess.run(["createdb", temporary], check=False).returncode or subprocess.run(
        ["pg_restore", "-d", temporary, "--no-owner", "-j", "2", str(dump)],
        capture_output=True, check=False,
    ).returncode > 1:
        say(f"{database}: the restore failed, the DB here is left as it was")
        subprocess.run(["dropdb", "--if-exists", "--force", temporary], check=False)
        return
    subprocess.run(["dropdb", "--if-exists", "--force", database], check=False)
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


def submit(worker, argv, kind, workers, base):
    cwd = pathlib.Path.cwd()
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
    snaps = sync(worker, base)
    for name in dbs_in:
        call(worker, "put", name, body=pack_database(name))
    spec = {
        "argv": argv,
        "cwd": str(cwd.relative_to(WORKSPACE)) if cwd != WORKSPACE else "",
        "kind": kind if kind in ("hoot", "odoo") else "other",
        "workers": workers,
        "snaps": snaps,
        "venv": pathlib.Path(os.environ.get("VIRTUAL_ENV") or "odoo20").name,
        "dbs_in": dbs_in,
        "dbs_out": dbs_out,
        "max_wait": MAX_WAIT,
    }
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
    if dbs_out:
        try:
            get_back(worker, job, dbs_out)
        except (Fallback, OSError, tarfile.TarError) as error:
            say(f"the DB did not come back: {error}")
    return code
