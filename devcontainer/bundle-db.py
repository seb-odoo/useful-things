#!/usr/bin/env python3
"""Give a bundle container the folders of its databases.

    python3 bundle-db.py <bundle folder name>     # initializeCommand, on the host

base.jsonc mounts three folders of SHARE_ROOT/bundle-db/<bundle>: `pg`, where the postgres server
of the bundle keeps its cluster, `run`, its socket at the PGHOST of the container, and `odoo`, the
filestore. A new bundle gets them at its creation (`prepare_worktree_bundle_folder`), `pg/16`
empty until its first start copies the cluster of the image there. A bundle made before its own
server had `run` and `odoo` linked to the host's server and filestore, which reach every database
(security review of 2026-10-28, M12): its next start makes them folders of its own, and an empty
`pg/16`, so it starts on a server of its own. Its old databases stay on the host's server.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import config  # noqa: E402


def main():
    values = config.load()
    folder = pathlib.Path(values["SHARE_ROOT"]) / "bundle-db" / sys.argv[1]
    for name in ("run", "odoo"):
        if (folder / name).is_symlink():
            (folder / name).unlink()
        (folder / name).mkdir(parents=True, exist_ok=True)
    (folder / "pg" / "16").mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    main()
