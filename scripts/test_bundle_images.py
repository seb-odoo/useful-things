#!/usr/bin/env python3
"""Which images go with a deleted bundle, without podman: python3 test_bundle_images.py"""

import sys

import delete_bundle

HASH = "0123456789abcdef" * 4
OTHER_HASH = "fedcba9876543210" * 4
REPOSITORIES = [
    f"localhost/vsc-master-foo--seb-{HASH}",
    f"localhost/vsc-master-foo--seb-{HASH}-uid",
    f"localhost/vsc-master-foo--seb-{OTHER_HASH}-uid",
    f"localhost/vsc-master-foo-bar--seb-{HASH}",
    f"localhost/vsc-master-foo-bar--seb-{HASH}-uid",
    f"localhost/vsc-master-foo--seb-2-{HASH}",
    f"localhost/vsc-saas-1912-foo--seb-{HASH}",
    f"localhost/vsc-host-ctl-{HASH}-uid",
    f"localhost/vsc-master-foo--seb-{HASH[:12]}",
    f"localhost/vsc-master-foo--seb-{HASH}-features",
    f"docker.io/library/vsc-master-foo--seb-{HASH}",
    "localhost/remote-runs-runner",
]
CASES = [
    (
        "master-foo--seb",
        [
            f"localhost/vsc-master-foo--seb-{HASH}",
            f"localhost/vsc-master-foo--seb-{HASH}-uid",
            f"localhost/vsc-master-foo--seb-{OTHER_HASH}-uid",
        ],
    ),
    (
        "master-foo-bar--seb",
        [
            f"localhost/vsc-master-foo-bar--seb-{HASH}",
            f"localhost/vsc-master-foo-bar--seb-{HASH}-uid",
        ],
    ),
    ("master-foo", []),
    ("saas-19.2-foo--seb", []),
    ("master-gone--seb", []),
]


def main():
    failures = []
    for bundle, expected in CASES:
        found = delete_bundle.bundle_images(bundle, REPOSITORIES)
        if found != expected:
            failures.append(f"{bundle}: {found}, expected {expected}")
    for line in failures:
        print(line)
    print(f"{len(CASES) - len(failures)}/{len(CASES)} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
