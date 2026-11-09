"""Add to the store the crates the Cargo.lock of o-sfu names, for cargo to read them offline.

    python3 cargo_crates.py <bundle>

A bundle has no network, so its cargo reads the crates of crates.io from a store only the host
writes, mounted read-only at /opt/cargo-crates (base.jsonc), as a directory source
(`~/.cargo/config.toml` of the Dockerfile). Each crate of the lock missing from the store is
downloaded from static.crates.io, checked against the sha256 the lock gives, and unpacked there
with the `.cargo-checksum.json` cargo asks of such a source. Nothing of the crate runs: no cargo
runs on the host. The lock is the bundle's, so it is read as data: a bounded regular file, each
name, version and checksum matched before it goes in a URL or a path, and the archive unpacked
file by file, regular files and folders only, under its own folder. `add_worktree` (utils.py)
runs it after each o-sfu worktree it adds or moves: at a create, a fetch, a build, an add-repo.
"""

from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request

from agents import read_bytes
from commands import get_worktree_bundle_repo_folder
from config import WORKTREE_CONTAINER

CHECKSUM = re.compile(r"[0-9a-f]{64}")
CRATES_IO = {
    "registry+https://github.com/rust-lang/crates.io-index",
    "sparse+https://index.crates.io/",
}
DOWNLOADS = 8
MAX_CRATE = 64 * 1024 * 1024
MAX_LOCK = 4 * 1024 * 1024
MAX_UNPACKED = 256 * 1024 * 1024
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
STORE = pathlib.Path(WORKTREE_CONTAINER) / ".cargo-crates"
URL = "https://static.crates.io/crates/{name}/{name}-{version}.crate"
VALUE = re.compile(r'^(\w+) = "([^"\\]*)"$')
VERSION = re.compile(r"[0-9A-Za-z.+-]{1,64}")


def parse_lock(text):
    """The (name, version, checksum) of each crate of crates.io the lock names, and the lines of
    the packages left out: a package of the workspace has no source and is no crate to fetch."""
    crates, left = [], []
    for block in text.split("[[package]]")[1:]:
        values = dict(m.groups() for line in block.splitlines() if (m := VALUE.match(line.strip())))
        source = values.get("source")
        if source is None:
            continue
        name, version, checksum = (values.get(key, "") for key in ("name", "version", "checksum"))
        if (
            source in CRATES_IO
            and NAME.fullmatch(name)
            and VERSION.fullmatch(version)
            and CHECKSUM.fullmatch(checksum)
        ):
            crates.append((name, version, checksum))
        else:
            left.append(f"{name[:64]} {version[:64]} from {source[:100]}")
    return crates, left


def download(url):
    with urllib.request.urlopen(url, timeout=60) as answer:
        data = answer.read(MAX_CRATE + 1)
    if len(data) > MAX_CRATE:
        raise ValueError(f"over {MAX_CRATE} bytes")
    return data


def unpack(data, top, folder):
    """Unpack the .crate archive `data` into `folder`, from its folder `top`: regular files and
    folders only, each under `top`, and MAX_UNPACKED bytes at most."""
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            parts = pathlib.PurePosixPath(member.name).parts
            if not parts or parts[0] != top or any(part in ("", ".", "..") for part in parts):
                raise ValueError(f"{member.name[:200]!r} is outside {top}/")
            path = folder.joinpath(*parts[1:])
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                total += member.size
                if total > MAX_UNPACKED:
                    raise ValueError(f"over {MAX_UNPACKED} bytes unpacked")
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, open(path, "xb") as target:
                    shutil.copyfileobj(source, target)
                path.chmod(0o755 if member.mode & 0o100 else 0o644)


def add_crate(store, name, version, checksum, fetch):
    """Put the crate in the store, unless it is there. It goes in a folder of its own first, then
    takes its name at once, never over a crate there: a cargo that reads the store meanwhile never
    sees half of one."""
    final = store / f"{name}-{version}"
    if final.exists():
        return False
    data = fetch(URL.format(name=name, version=version))
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError("its sha256 is not the one of the lock")
    work = pathlib.Path(tempfile.mkdtemp(prefix=".new-", dir=store))
    try:
        unpack(data, f"{name}-{version}", work)
        # No file listed: cargo checks the archive by the sha256 of the lock, the files are ours.
        (work / ".cargo-checksum.json").write_text(json.dumps({"files": {}, "package": checksum}))
        work.chmod(0o755)
        try:
            work.rename(final)
        except OSError:
            return False
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return True


def fill(lock, store=STORE, fetch=download):
    """Add the crates of the lock `lock` (bytes) the store misses. Says what it did, and returns
    the number of crates it could not add."""
    try:
        text = lock.decode()
    except UnicodeDecodeError:
        print("Cargo.lock is no text: no crate added")
        return 1
    crates, left = parse_lock(text)
    for line in left:
        print(f"Cargo.lock: {line} is no crate of crates.io, left out")
    store.mkdir(exist_ok=True)

    def add(crate):
        try:
            return add_crate(store, *crate, fetch)
        except (OSError, ValueError, EOFError, tarfile.TarError, gzip.BadGzipFile) as error:
            print(f"{crate[0]} {crate[1]}: {error}")
            return None

    with ThreadPoolExecutor(DOWNLOADS) as pool:
        added = list(pool.map(add, crates))
    failed = added.count(None)
    print(
        f"o-sfu: {added.count(True)} crates added to the store, {len(crates)} in its Cargo.lock"
        + (f", {failed} missing: cargo cannot build offline what needs them" if failed else "")
    )
    return failed


def fill_bundle(bundle_name):
    """Add the crates of the Cargo.lock of the o-sfu worktree of the bundle, when it has one."""
    folder = get_worktree_bundle_repo_folder(bundle_name, "o-sfu")
    if not os.path.isdir(folder):
        return 0
    lock = read_bytes(f"{folder}/Cargo.lock", MAX_LOCK + 1)
    if lock is None or len(lock) > MAX_LOCK:
        print(f"o-sfu: no Cargo.lock of {MAX_LOCK} bytes at most, no crate added")
        return 1
    return fill(lock)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip())
    sys.exit(1 if fill_bundle(sys.argv[1]) else 0)
