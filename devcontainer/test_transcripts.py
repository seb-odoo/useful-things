#!/usr/bin/env python3
"""What transcripts.py makes for a bundle, in a temporary folder: python3 test_transcripts.py

Each bundle writes a transcripts folder of its own and reads the others read-only, a bundle on an
outside PR none (security check 11-03, B7). Its session comes from the old shared folder, with
no link followed.
"""

import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SESSION = "8b9b2f6e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
failures = []


def check(what, got, expected):
    if got != expected:
        failures.append(f"{what}: got {got!r}, expected {expected!r}")


def run(root, bundle, folder):
    env = {
        **os.environ,
        "HOME": str(root / "home"),
        "CACHE_ROOT": str(root / "cache"),
        "SHARE_ROOT": str(root / "share"),
        "XDG_CONFIG_HOME": str(root / "config"),
    }
    command = [sys.executable, str(HERE / "transcripts.py"), bundle, str(folder)]
    return subprocess.run(command, capture_output=True, check=False, env=env, text=True)


def main():
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        old = root / "home" / ".claude" / "projects" / "-workspace"
        (old / SESSION / "subagents").mkdir(parents=True)
        (old / f"{SESSION}.jsonl").write_text("turns\n")
        (old / SESSION / "subagents" / "a.jsonl").write_text("sub\n")
        secret = root / "secret"
        secret.write_text("key\n")
        (old / SESSION / "link").symlink_to(secret)
        configs = root / "cache" / "devcontainer" / "claude-config"
        mine = root / "share" / "claude-transcripts"
        for bundle in ("b1", "b2", "b3"):
            (root / bundle / ".agent").mkdir(parents=True)
            (root / bundle / ".agent" / "session").write_text(f"{SESSION}\n")
        (configs / "b2").mkdir(parents=True)
        (configs / "b2" / "outside").write_text("{}\n")
        victim = root / "victim"
        victim.mkdir()
        (mine / "b3").mkdir(parents=True)
        (mine / "b3" / SESSION).symlink_to(victim)
        for bundle in ("b1", "b2", "b3"):
            res = run(root, bundle, root / bundle)
            check(f"{bundle}: exit code", (res.returncode, res.stderr), (0, ""))
        check("its session copied", (mine / "b1" / f"{SESSION}.jsonl").read_text(), "turns\n")
        check("with its folder", (mine / "b1" / SESSION / "subagents" / "a.jsonl").read_text(), "sub\n")
        check("but no link", os.path.lexists(mine / "b1" / SESSION / "link"), False)
        check("the others, read", os.readlink(configs / "b1" / "transcripts"), str(mine))
        check("and before", os.readlink(configs / "b1" / "transcripts-before"), str(old))
        empty = str(configs / "b2" / "empty")
        found = [os.readlink(configs / "b2" / name) for name in ("transcripts", "transcripts-before")]
        check("an outside bundle reads none", found, [empty, empty])
        check("no copy through a link of the bundle", list(victim.iterdir()), [])
        (mine / "b1" / f"{SESSION}.jsonl").write_text("mine\n")
        run(root, "b1", root / "b1")
        check("its own copy stays", (mine / "b1" / f"{SESSION}.jsonl").read_text(), "mine\n")
        (root / "b1" / ".agent" / "session").unlink()
        (root / "b1" / ".agent" / "session").symlink_to(secret)
        check("a link at .agent/session", run(root, "b1", root / "b1").returncode, 0)
    for line in failures:
        print(line)
    print("all as expected" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
