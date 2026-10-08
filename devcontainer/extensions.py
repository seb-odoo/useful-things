#!/usr/bin/env python3
"""Give one bundle container its own VS Code extension store.

    python3 extensions.py <bundle folder name> <bundle folder>   # initializeCommand, on the host
    python3 extensions.py --remove <extension id>                # drop it from every store

A VS Code server rewrites extensions.json in place and locks nothing between processes: two
servers on one store end with a file that does not parse, and every window opened after that
loads no extension. So each bundle mounts a store of its own, cloned with hard links from a
template only this script writes, and a new bundle still starts with every extension installed.

Before each start, under a lock: what the server of the bundle installed goes to the template
(per extension, the greater version then the later install wins), then a bundle that differs from
the template is cloned again. Only while its container is down, as a running server is the one
writer of its store. An uninstall in a bundle is not followed: --remove lists the extension in
the template's `removed` file, delete its line there to allow it again.
"""

import fcntl
import json
import pathlib
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import config  # noqa: E402

MANIFEST = "extensions.json"


def read(store):
    """The entries of a store by extension id, None when its manifest cannot be read."""
    try:
        by_id = {}
        for entry in json.loads((store / MANIFEST).read_text() or "[]"):
            name = entry["relativeLocation"]
            folder = store / name
            # Take only a plain folder name, as a container writes the manifest.
            if "/" in name or name.startswith(".") or folder.is_symlink() or not folder.is_dir():
                continue
            by_id.setdefault(entry["identifier"]["id"].lower(), []).append(entry)
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return None
    return by_id


def write(store, by_id):
    tmp = store / f".{MANIFEST}.tmp"
    tmp.write_text(json.dumps([entry for name in sorted(by_id) for entry in by_id[name]]))
    tmp.replace(store / MANIFEST)


def key(entries):
    return max(
        (
            tuple(int(part) for part in re.findall(r"\d+", entry["version"])),
            (entry.get("metadata") or {}).get("installedTimestamp", 0),
        )
        for entry in entries
    )


def state(by_id):
    if by_id is None:
        return None
    return {(e["relativeLocation"], key([e])) for entries in by_id.values() for e in entries}


def folders(by_id):
    return {entry["relativeLocation"] for entries in by_id.values() for entry in entries}


def link(source, target):
    tmp = target.with_name(f".{target.name}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    subprocess.run(["cp", "-al", str(source), str(tmp)], check=True)
    shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)


def prune(store, by_id):
    keep = folders(by_id)
    for path in store.iterdir():
        if path.is_dir() and path.name not in keep:
            shutil.rmtree(path, ignore_errors=True)


def harvest(source, root, template, store, skip):
    """Take into the template the extensions `source` (a store at `root`) has newer."""
    changed = False
    for name, entries in source.items():
        if name in skip or (name in template and key(entries) <= key(template[name])):
            continue
        for entry in entries:
            link(root / entry["relativeLocation"], store / entry["relativeLocation"])
        template[name] = entries
        changed = True
    if changed:
        write(store, template)
        prune(store, template)


def clone(template, store, own, mine):
    keep = {name for name, _ in (state(mine) or set()) & state(template)}
    for path in own.iterdir():
        if path.name in keep:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    sources = [str(store / name) for name in sorted(folders(template) - keep)]
    if sources:
        subprocess.run(["cp", "-al", "-t", str(own), *sources], check=True)
    write(own, template)


def is_running(folder):
    res = subprocess.run(
        ["podman", "ps", "--quiet", "--filter", f"label=devcontainer.local_folder={folder}"],
        capture_output=True,
        check=False,
        text=True,
    )
    return res.returncode != 0 or bool(res.stdout.split())


def remove(template, store, name):
    name = name.lower()
    if name not in template:
        sys.exit(f"{name}: not in the template, which has:\n  " + "\n  ".join(sorted(template)))
    gone = template.pop(name)
    with (store / "removed").open("a") as removed:
        removed.write(f"{name}\n")
    write(store, template)
    prune(store, template)
    print(f"removed {name} ({', '.join(entry['version'] for entry in gone)}) from the template")
    print("a bundle loses it at its next start with its container down")
    print(f"to allow it again, delete its line in {store / 'removed'}")


def sync(template, store, root, own, folder):
    removed = store / "removed"
    skip = set(removed.read_text().split()) if removed.is_file() else set()
    if not template:
        old = root / "vscode-server-extensions"
        harvest(read(old) or {}, old, template, store, skip)
    mine = read(own)
    if mine:
        harvest(mine, own, template, store, skip)
    if not template or state(mine) == state(template):
        return
    if not any(own.iterdir()) or not is_running(folder):
        clone(template, store, own, mine)


def main():
    root = pathlib.Path(config.load()["CACHE_ROOT"]) / "devcontainer"
    store = root / "vscode-extensions-template"
    own = None
    if sys.argv[1] != "--remove":
        bundle = sys.argv[1]
        if not bundle or "/" in bundle or bundle.startswith("."):
            sys.exit(f"not a bundle folder name: {bundle!r}")
        own = root / "vscode-extensions" / bundle
        own.mkdir(parents=True, exist_ok=True)
    store.mkdir(parents=True, exist_ok=True)
    with (store / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        template = read(store) or {}
        if own:
            sync(template, store, root, own, sys.argv[2])
        else:
            remove(template, store, sys.argv[2])


if __name__ == "__main__":
    # Never fail, as a failing initializeCommand stops the container from starting.
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"extensions.py: {error!r}", file=sys.stderr)
