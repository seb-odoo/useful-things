#!/usr/bin/env python3
"""Give a bundle container the folders of its databases.

    python3 bundle-db.py <bundle folder name>     # initializeCommand, on the host

base.jsonc mounts three folders of SHARE_ROOT/bundle-db/<bundle>: `pg`, where the postgres server
of the bundle keeps its cluster, `run`, its socket at the PGHOST of the container, and `odoo`, the
filestore. A new bundle gets them at its creation (`finish_worktree_bundle_folder`), `pg/16` empty
until its first start copies the cluster of the image there. A bundle made before has none and
keeps the host's server and filestore: `run` and `odoo` link to them, and `pg` has no `16`, so
container-rw/bundle-pg.sh starts nothing.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import config  # noqa: E402


def main():
    values = config.load()
    folder = pathlib.Path(values["SHARE_ROOT"]) / "bundle-db" / sys.argv[1]
    try:
        folder.mkdir(parents=True)
    except FileExistsError:
        return
    (folder / "pg").mkdir()
    (folder / "run").symlink_to(values["PG_HOST"])
    (folder / "odoo").symlink_to(pathlib.Path(values["SHARE_ROOT"]) / "Odoo")


if __name__ == "__main__":
    main()
