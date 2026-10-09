#!/usr/bin/env python3
"""Which containers of a folder an open removes, without podman: python3 test_containers.py"""

import sys

import commands

CONFIG = "/src/.devcontainer/devcontainer.json"
OTHER = "/src/master/master-fix--me/.devcontainer/devcontainer.json"
CHANGED = 1000

CASES = [
    ([("a", "exited", 900, CONFIG)], (["a"], [])),
    ([("a", "exited", 1100, CONFIG)], ([], [])),
    ([("a", "exited", 1100, OTHER)], (["a"], [])),
    ([("a", "created", 900, CONFIG)], (["a"], [])),
    ([("a", "running", 900, CONFIG)], ([], ["a"])),
    ([("a", "running", 1100, OTHER)], ([], ["a"])),
    ([("a", "running", 1100, CONFIG)], ([], [])),
    ([("a", "exited", 900, OTHER), ("b", "running", 1100, CONFIG)], (["a"], [])),
    ([], ([], [])),
]
FOLDERS = [
    ("/src/master/master-fix--me", "/repo/tools", True),
    ("/repo/tools", "/repo/tools", True),
    ("/repo/tools/", "/repo/tools", True),
    ("/repo/other", "/repo/tools", False),
    ("/repo/tools", None, False),
]


def main():
    failures = []
    for rows, expected in CASES:
        found = commands.get_stale_containers(rows, CONFIG, CHANGED)
        if found != expected:
            failures.append(f"{rows}: {found}, expected {expected}")
    commands.WORKTREE_CONTAINER = "/src"
    for folder, tools, expected in FOLDERS:
        commands.HOST_FOLDER = tools
        if commands.is_replaceable(folder) != expected:
            failures.append(f"{folder} with the tools in {tools}: not {expected}")
    for line in failures:
        print(line)
    total = len(CASES) + len(FOLDERS)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
