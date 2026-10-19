#!/usr/bin/env python3
"""bundle-pg.sh on a fake server, run as a start then an agent terminal: python3 test_bundle_pg.py"""

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "container-rw" / "bundle-pg.sh"
SERVER = "bundlepgtest"


def main():
    failures = []
    with tempfile.TemporaryDirectory() as root:
        root = pathlib.Path(root)
        (root / "cache").mkdir()
        (root / "cache" / "PG_VERSION").write_text("16\n")
        (root / "run").mkdir()
        data = root / "home" / ".local" / "share" / "postgresql" / "16"
        data.mkdir(parents=True)
        shutil.copy("/bin/sleep", root / SERVER)
        pg_ctl = root / "pg_ctl"
        pg_ctl.write_text(
            f'#!/bin/bash\n"{root / SERVER}" 60 < /dev/null > /dev/null 2>&1 &\necho $! >> "{root}/pids"\n'
        )
        pg_ctl.chmod(0o755)
        script = SCRIPT.read_text()
        for old, new in (
            ("/usr/lib/postgresql/16/bin/pg_ctl", str(pg_ctl)),
            ("/opt/bundle-pg/16", str(root / "cache")),
            ("/var/run/postgresql", str(root / "run")),
            ("-x postgres", f"-x {SERVER}"),
        ):
            if old not in script:
                print(f"{SCRIPT}: no {old}, the test needs a new fake")
                return 1
            script = script.replace(old, new)
        (root / "bundle-pg.sh").write_text(script)
        env = {"HOME": str(root / "home"), "PATH": os.environ["PATH"]}
        try:
            for name in ("first start", "second start"):
                try:
                    res = subprocess.run(
                        ["bash", root / "bundle-pg.sh"],
                        capture_output=True,
                        check=False,
                        env=env,
                        text=True,
                        timeout=10,
                    )
                except subprocess.TimeoutExpired:
                    failures.append(f"{name}: still running after 10 s")
                    break
                if res.returncode:
                    failures.append(f"{name}: rc={res.returncode} {res.stderr.strip()}")
            if not (data / "PG_VERSION").exists():
                failures.append("the cluster of the image was not copied")
            elif data.stat().st_mode & 0o777 != 0o700:
                failures.append(f"cluster mode {data.stat().st_mode & 0o777:o}, expected 700")
            if (count := len((root / "pids").read_text().split())) != 1:
                failures.append(f"{count} servers started, expected 1")
        finally:
            for pid in (root / "pids").read_text().split() if (root / "pids").exists() else ():
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
    print("\n".join(failures))
    print(f"bundle-pg.sh: {len(failures)} problems")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
