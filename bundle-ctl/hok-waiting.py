#!/usr/bin/env python3
"""`hok waiting` of host-ctl for the daemon, in a process that stays: each line on stdin gets one
JSON line on stdout, the list `approve.py waiting` prints and whether the code of hok changed
since it was loaded, or the error.

    python3 hok-waiting.py PATH/approve.py

What podman says of the containers is kept MOUNTS seconds, as the hok window keeps it: hok asks it
again for each request at each read, 0.85 s of CPU with 66 containers, and `approve.py waiting`
started for each read every 5 s while an agent terminal shows its band.
"""

import importlib.util
import json
import pathlib
import sys
import threading
import time

MOUNTS = 30


def keep(function, seconds):
    """function, its answer kept for seconds: a None, podman that did not answer, is asked again
    at the next call, and so is any answer after cache_clear."""
    lock, kept = threading.Lock(), {"answer": None, "until": 0}

    def call():
        with lock:
            if kept["answer"] is None or time.monotonic() >= kept["until"]:
                kept["answer"] = function()
                kept["until"] = time.monotonic() + seconds
            return kept["answer"]

    def clear():
        kept["until"] = 0

    call.cache_clear = clear
    return call


def load(path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("approve", path)
    approve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(approve)
    approve.loaded.append(approve.get_version())
    approve.mounts.read = keep(approve.mounts.read, MOUNTS)
    approve.get_containers = keep(approve.get_containers.__wrapped__, MOUNTS)
    return approve


def serve(approve, source, sink):
    for _line in source:
        try:
            answer = {"listed": approve.list_waiting(), "stale": approve.is_stale()}
        except (Exception, SystemExit) as error:  # noqa: BLE001
            answer = {"error": f"{type(error).__name__}: {error}"[:300]}
        sink.write(json.dumps(answer) + "\n")
        sink.flush()


def main():
    channel, sys.stdout = sys.stdout, sys.stderr
    serve(load(pathlib.Path(sys.argv[1])), sys.stdin, channel)


if __name__ == "__main__":
    main()
