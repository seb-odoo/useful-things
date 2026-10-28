#!/usr/bin/env python3
"""What bundle-db.py gives a bundle before its start, in a throwaway share folder:
python3 test_bundle_db.py"""

import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
results = []


def check(name, got, expected):
    results.append(got == expected)
    if got != expected:
        print(f"FAIL {name}: got {got!r}, expected {expected!r}")


def run(root, bundle):
    env = {"PATH": os.environ["PATH"], "HOME": str(root), "SHARE_ROOT": str(root / "share")}
    return subprocess.run(
        [sys.executable, "-B", str(HERE / "bundle-db.py"), bundle],
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )


def main():
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        server, filestore = root / "host-run", root / "share" / "Odoo"
        for path in (server, filestore):
            path.mkdir(parents=True)
            (path / "kept").touch()
        old = root / "share" / "bundle-db" / "old"
        (old / "pg").mkdir(parents=True)
        (old / "run").symlink_to(server)
        (old / "odoo").symlink_to(filestore)
        check("an old bundle: exit code", run(root, "old").returncode, 0)
        for part in ("run", "odoo", "pg/16"):
            path = old / part
            check(
                f"its {part} is a folder of its own",
                (path.is_dir(), path.is_symlink()),
                (True, False),
            )
        check("the host's server is left as it is", sorted(os.listdir(server)), ["kept"])
        check("and its filestore", sorted(os.listdir(filestore)), ["kept"])

        check("a bundle with no folder yet", run(root, "new").returncode, 0)
        new = root / "share" / "bundle-db" / "new"
        check(
            "gets them all", all((new / part).is_dir() for part in ("run", "odoo", "pg/16")), True
        )
        (new / "pg" / "16" / "PG_VERSION").write_text("16\n")
        run(root, "new")
        check("a cluster there is kept", (new / "pg" / "16" / "PG_VERSION").read_text(), "16\n")
    print(f"{sum(results)}/{len(results)} checks as expected")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
