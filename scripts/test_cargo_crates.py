#!/usr/bin/env python3
"""The crates cargo_crates.py adds to the store from a Cargo.lock, and the locks and archives it
refuses, with crates.io faked: python3 test_cargo_crates.py
"""

import hashlib
import io
import json
import pathlib
import sys
import tarfile
import tempfile

import cargo_crates

WORKSPACE = '[[package]]\nname = "o-sfu-core"\nversion = "0.12.0"\ndependencies = [\n "serde",\n]\n'


def archive(top, files, links=()):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        for name, (text, mode) in files.items():
            info = tarfile.TarInfo(f"{top}/{name}" if top else name)
            info.size, info.mode = len(text), mode
            tar.addfile(info, io.BytesIO(text))
        for name, target in links:
            info = tarfile.TarInfo(f"{top}/{name}")
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    return data.getvalue()


def lock_of(*crates, source="registry+https://github.com/rust-lang/crates.io-index"):
    text = "version = 4\n\n" + WORKSPACE
    for name, version, data in crates:
        checksum = hashlib.sha256(data).hexdigest() if isinstance(data, bytes) else data
        text += (
            f'\n[[package]]\nname = "{name}"\nversion = "{version}"\nsource = "{source}"\n'
            f'checksum = "{checksum}"\n'
        )
    return text.encode()


def check(root):
    checks = []
    served, asked = {}, []

    def fetch(url):
        asked.append(url)
        return served[url]

    def url(name, version):
        return cargo_crates.URL.format(name=name, version=version)

    def fill(lock):
        asked.clear()
        return cargo_crates.fill(lock, store, fetch)

    store = root / "store"
    good = archive("serde-1.0.0", {"Cargo.toml": (b"[package]\n", 0o644), "run.sh": (b"", 0o755)})
    served[url("serde", "1.0.0")] = good
    checks.append(("a crate of the lock comes in the store", fill(lock_of(("serde", "1.0.0", good))) == 0))
    folder = store / "serde-1.0.0"
    checks.append(
        (
            "with its files, their modes, and the checksum of the lock for cargo",
            (
                (folder / "Cargo.toml").read_bytes(),
                oct((folder / "run.sh").stat().st_mode & 0o777),
                json.loads((folder / ".cargo-checksum.json").read_text()),
            )
            == (b"[package]\n", "0o755", {"files": {}, "package": hashlib.sha256(good).hexdigest()}),
        )
    )
    checks.append(("the workspace is no crate to fetch", asked == [url("serde", "1.0.0")]))
    fill(lock_of(("serde", "1.0.0", good)))
    checks.append(("a crate there is not fetched again", asked == []))
    sparse = lock_of(("serde", "1.0.0", good), source="sparse+https://index.crates.io/")
    checks.append(("the sparse index is crates.io too", cargo_crates.parse_lock(sparse.decode())[1] == []))

    other = archive("log-0.4.0", {"Cargo.toml": (b"x", 0o644)})
    served[url("log", "0.4.0")] = other
    checks.append(
        (
            "a sha256 the lock does not give leaves no crate and no work folder",
            (fill(lock_of(("log", "0.4.0", "0" * 64))), sorted(p.name for p in store.iterdir()))
            == (1, ["serde-1.0.0"]),
        )
    )
    for case, data in (
        ("a path out of its folder", archive("", {"evil-1.0.0/../escaped": (b"x", 0o644)})),
        ("a file of another folder", archive("", {"other/file": (b"x", 0o644)})),
        ("an absolute path", archive("", {"/tmp/escaped": (b"x", 0o644)})),
        ("no archive", b"not gzip"),
    ):
        served[url("evil", "1.0.0")] = data
        refused = fill(lock_of(("evil", "1.0.0", data)))
        checks.append(
            (
                f"{case} is refused, nothing written",
                (refused, sorted(p.name for p in store.iterdir()), (root / "escaped").exists())
                == (1, ["serde-1.0.0"], False),
            )
        )
    linked = archive("link-1.0.0", {"Cargo.toml": (b"x", 0o644)}, [("passwd", "/etc/passwd")])
    served[url("link", "1.0.0")] = linked
    fill(lock_of(("link", "1.0.0", linked)))
    checks.append(
        (
            "a link of the archive is left out, its files come",
            (
                (store / "link-1.0.0" / "Cargo.toml").exists(),
                (store / "link-1.0.0" / "passwd").exists(),
            )
            == (True, False),
        )
    )
    crates, left = cargo_crates.parse_lock(
        lock_of(("a/b", "1.0.0", "0" * 64), ("c", "1 0", "0" * 64), ("d", "1.0.0", "short")).decode()
        + lock_of(("e", "1.0.0", "0" * 64), source="git+https://example.com/e?rev=1").decode()
    )
    checks.append(
        (
            "a name, a version or a checksum out of form, or a source other than crates.io, is left out",
            (crates, len(left)) == ([], 4),
        )
    )
    checks.append(("a lock that is no text adds nothing", fill(b"\xff\xfe") == 1 and asked == []))
    twice = archive("dup-1.0.0", {"Cargo.toml": (b"x", 0o644)})
    served[url("dup", "1.0.0")] = twice
    fill(lock_of(*[("dup", "1.0.0", twice)] * 4))
    checks.append(
        (
            "a crate the lock names four times comes once, no work folder left",
            sorted(p.name for p in store.iterdir() if p.name.startswith((".", "dup")))
            == ["dup-1.0.0"],
        )
    )
    return [name for name, passed in checks if not passed], len(checks)


def main():
    with tempfile.TemporaryDirectory() as root:
        failures, total = check(pathlib.Path(root))
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
