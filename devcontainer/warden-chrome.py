#!/usr/bin/env python3
"""Put the browser of the Hoot runs in CACHE_ROOT/warden, which every bundle mounts read-only.

    python3 warden-chrome.py     # initializeCommand, through initialize.py, on the host

When no chrome is set, the TestWarden engine runs
~/.cache/warden/chrome-headless-shell/chrome-headless-shell, and downloads it there when it is
missing. A bundle that could write it would choose the browser of every other bundle and of the
host, so only the host writes it: once, the Stable of Chrome for Testing, as the engine does. For
a newer one, delete the folder on the host, and the next start of a bundle downloads it again.
"""

import io
import json
import os
import pathlib
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import config  # noqa: E402

NAME = "chrome-headless-shell"
VERSIONS = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)


def get_url():
    with urllib.request.urlopen(VERSIONS, timeout=30) as answer:
        downloads = json.load(answer)["channels"]["Stable"]["downloads"][NAME]
    return next(entry["url"] for entry in downloads if entry["platform"] == "linux64")


def main():
    cache = pathlib.Path(config.load()["CACHE_ROOT"]) / "warden"
    cache.mkdir(parents=True, exist_ok=True)
    if (cache / NAME / NAME).is_file():
        return
    try:
        url = get_url()
        print(f"downloading {url} into {cache}, once", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=600) as answer:
            archive = zipfile.ZipFile(io.BytesIO(answer.read()))
    except (OSError, KeyError, StopIteration, ValueError) as error:
        sys.exit(f"{NAME} is not downloaded, so the Hoot runs of a bundle fail: {error!r}")
    with tempfile.TemporaryDirectory(dir=cache) as fresh:
        for entry in archive.infolist():
            path = archive.extract(entry, fresh)
            if not entry.is_dir():
                os.chmod(path, entry.external_attr >> 16 & 0o777 or 0o644)
        try:
            (pathlib.Path(fresh) / f"{NAME}-linux64").rename(cache / NAME)
        except OSError:
            if not (cache / NAME / NAME).is_file():
                raise


if __name__ == "__main__":
    main()
