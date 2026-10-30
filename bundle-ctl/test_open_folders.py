#!/usr/bin/env python3
"""When the daemon asks podman again for the running containers: python3 test_open_folders.py"""

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon  # noqa: E402


def main():
    asked = []
    running = {"/b/one"}

    def get_open_bundle_folders():
        asked.append(1)
        return set(running)

    daemon.get_open_bundle_folders = get_open_bundle_folders
    clock = [1000.0]
    daemon.time.monotonic = lambda: clock[0]
    failures = []
    daemon.read_open_folders()
    daemon.read_open_folders()
    if len(asked) != 1:
        failures.append(f"podman asked {len(asked)} times with no event, expected 1")
    running.add("/b/two")
    daemon.note_container_event()
    if daemon.read_open_folders() != {"/b/one", "/b/two"} or len(asked) != 2:
        failures.append("a started container not seen at the next read")
    clock[0] += daemon.FOLDERS_KEPT
    daemon.read_open_folders()
    if len(asked) != 3:
        failures.append(f"podman not asked again after {daemon.FOLDERS_KEPT} s")

    def racing():
        asked.append(1)
        found = set(running)
        running.discard("/b/two")
        daemon.note_container_event()
        return found

    clock[0] += daemon.FOLDERS_KEPT
    daemon.get_open_bundle_folders = racing
    daemon.read_open_folders()
    daemon.get_open_bundle_folders = get_open_bundle_folders
    if daemon.read_open_folders() != {"/b/one"}:
        failures.append("a container that died while podman listed them was kept")
    for line in failures:
        print(line)
    print(f"{4 - len(failures)}/4 cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
