#!/usr/bin/env python3
"""Give one bundle container its VS Code extension store, and keep the template they come from.

    python3 extensions.py <bundle folder name> <bundle folder>   # initializeCommand, on the host
    python3 extensions.py --add <extension id or .vsix>...       # install it in the template
    python3 extensions.py --update [<extension id>...]           # the newest version, of all or these
    python3 extensions.py --remove <extension id>                # drop it from every store
    python3 extensions.py --store <folder> --add|--update ...    # on another store than the template

Each bundle mounts a store of its own, read-only, cloned with hard links from a template only this
script writes. Code in an extension runs in a VS Code window, and a window can type in a terminal
of the host: so a container must not be able to change an extension, its own or another bundle's.
A store per bundle and not the template itself, so that an update of the template leaves a running
window on the files it loaded.

An extension is installed or updated in the template by the installer of the VS Code server, run in
a throwaway container that mounts nothing else. Before each start, under a lock, a bundle whose
container is down and whose store differs from the template is cloned again. --remove lists the
extension in the template's `removed` file, delete its line there to allow it again. --store
names the store of a container that is no bundle and mounts it read-only the same way.
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

# Where a dev container mounts its store: the installer records that path in the manifest.
INSIDE = "/home/vscode/.vscode-server/extensions"
INSTALLER = (
    'server=$(ls -t /vscode/vscode-server/bin/linux-x64/*/bin/code-server | head -n1) &&'
    f' exec "$server" --extensions-dir {INSIDE} "$@"'
)
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


def installed(entry):
    metadata = entry.get("metadata") or {}
    # Compare the install time of a .vsix only, as a failed gallery update rewrites that time.
    return metadata.get("installedTimestamp", 0) if metadata.get("source") == "vsix" else 0


def key(entries):
    return max(
        (tuple(int(part) for part in re.findall(r"\d+", entry["version"])), installed(entry))
        for entry in entries
    )


def inode(folder):
    try:
        return (folder / "package.json").stat().st_ino
    except OSError:
        return 0


def state(store, by_id):
    if by_id is None:
        return None
    return {
        (entry["relativeLocation"], key([entry]), inode(store / entry["relativeLocation"]))
        for entries in by_id.values()
        for entry in entries
    }


def folders(by_id):
    return {entry["relativeLocation"] for entries in by_id.values() for entry in entries}


def prune(store, by_id):
    keep = folders(by_id)
    for path in store.iterdir():
        if path.is_dir() and path.name not in keep:
            shutil.rmtree(path, ignore_errors=True)


def listed():
    """The extensions the dev container config of the bundles asks for."""
    path = pathlib.Path(config.load()["WORKTREE_ROOT"]) / ".devcontainer/devcontainer.json"
    try:
        lines = [line for line in path.read_text().splitlines() if line[:2] != "//"]
        return json.loads("\n".join(lines))["customizations"]["vscode"]["extensions"]
    except (KeyError, OSError, ValueError):
        return []


def install(store, names, in_use=False):
    """Install extensions in the template, by id or .vsix, at their newest version."""
    if not names:
        return 0
    volumes, args = [], []
    for name in names:
        path = pathlib.Path(name)
        if path.suffix == ".vsix" and path.is_file():
            volumes += ["--volume", f"{path.resolve()}:/vsix/{path.name}:ro"]
            name = f"/vsix/{path.name}"
        args += ["--install-extension", name]
    # No keep-id: the installer runs as the root of the container, which is the host user then.
    # With keep-id it is a sub-uid, whose files the host user can neither hard-link nor remove.
    command = ["podman", "run", "--rm", "--cap-drop=ALL"]
    command += ["--security-opt=no-new-privileges", "--tmpfs", "/tmp", "--env", "HOME=/tmp"]
    # The home of the image belongs to its own user: without capabilities, root cannot enter it.
    command += ["--tmpfs", "/home/vscode"]
    command += ["--volume", "vscode:/vscode:ro", "--volume", f"{store}:{INSIDE}", *volumes]
    command += [config.load()["CONTAINER_BASE_IMAGE"], "sh", "-c", INSTALLER, "sh", *args, "--force"]
    res = subprocess.run(command, check=False)
    removed = store / "removed"
    skip = set(removed.read_text().split()) if removed.is_file() else set()
    template = {name: entries for name, entries in (read(store) or {}).items() if name not in skip}
    write(store, template)
    # A store a container mounts itself keeps the version a running window loaded.
    if not in_use:
        prune(store, template)
    return res.returncode


def clone(template, store, own, mine):
    keep = {name for name, *_ in (state(own, mine) or set()) & state(store, template)}
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


def sync(template, store, own, folder):
    if not template and install(store, listed()) == 0:
        template.update(read(store) or {})
    mine = read(own)
    if not template or state(own, mine) == state(store, template):
        return
    if not any(own.iterdir()) or not is_running(folder):
        clone(template, store, own, mine)


def main():
    root = pathlib.Path(config.load()["CACHE_ROOT"]) / "devcontainer"
    store = root / "vscode-extensions-template"
    args, in_use = sys.argv[1:], False
    if args[:1] == ["--store"]:
        store, args, in_use = pathlib.Path(args[1]), args[2:], True
    command, names = args[0], args[1:]
    own = None
    if not command.startswith("--"):
        if not command or "/" in command or command.startswith("."):
            sys.exit(f"not a bundle folder name: {command!r}")
        own = root / "vscode-extensions" / command
        own.mkdir(parents=True, exist_ok=True)
    store.mkdir(parents=True, exist_ok=True)
    with (store / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        template = read(store) or {}
        if own:
            sync(template, store, own, names[0])
        elif command == "--remove":
            remove(template, store, names[0])
        elif command in ("--add", "--update"):
            gallery = [name for name, entries in template.items() if not installed(entries[0])]
            names = names or (gallery if command == "--update" else [])
            sys.exit(install(store, names, in_use))
        else:
            sys.exit(__doc__.strip())


if __name__ == "__main__":
    # Never fail, as a failing initializeCommand stops the container from starting.
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"extensions.py: {error!r}", file=sys.stderr)
