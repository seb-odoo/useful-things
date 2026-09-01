#!/usr/bin/env python3
"""The per-machine config: ~/.config/odoo-dev/config.env, see config.env.example.

Precedence, first hit wins: the environment, config.env in this repo, ~/.config/odoo-dev/config.env,
the default. No absolute path belongs in a committed file, so everything here is either derived from
the home directory or overridden in that config.
"""

import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
HOME = pathlib.Path.home()


def _xdg(variable, fallback):
    return pathlib.Path(os.environ.get(variable) or HOME / fallback)


def _files():
    yield REPO / "config.env"
    yield _xdg("XDG_CONFIG_HOME", ".config") / "odoo-dev" / "config.env"


def load():
    """Return every key as a string, defaults filled in."""
    values = {}
    for path in _files():
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            key, _, value = line.partition("=")
            key = key.strip()
            if not key.isupper() or not key.replace("_", "").isalnum():
                continue
            values.setdefault(key, value.strip())
    for key in list(values):
        if key in os.environ:
            values[key] = os.environ[key]
    for key, default in {
        "HOME": str(HOME),
        "REPO_ROOT": str(HOME / "repo"),
        "WORKTREE_ROOT": str(HOME / "src" / "odoo"),
        "CONFIG_ROOT": str(_xdg("XDG_CONFIG_HOME", ".config")),
        "STATE_ROOT": str(_xdg("XDG_STATE_HOME", ".local/state")),
        "CACHE_ROOT": str(_xdg("XDG_CACHE_HOME", ".cache")),
        "SHARE_ROOT": str(_xdg("XDG_DATA_HOME", ".local/share")),
        "VENV_ROOT": str(HOME / "virtualenvs"),
        "PG_USER": os.environ.get("USER", "odoo"),
        "PG_HOST": "/var/run/postgresql",
        "CONTAINER_BASE_IMAGE": "mcr.microsoft.com/devcontainers/base:ubuntu-22.04",
    }.items():
        values[key] = os.environ.get(key) or values.get(key) or default
    values["VENV"] = os.environ.get("VENV") or values.get("VENV") or f"{values['VENV_ROOT']}/odoo20"
    values["USEFUL_THINGS"] = str(REPO)
    return values


if __name__ == "__main__":
    for key, value in sorted(load().items()):
        print(f"{key:22} {value}")
