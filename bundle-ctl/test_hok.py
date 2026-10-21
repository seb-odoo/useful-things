#!/usr/bin/env python3
"""What GET /hok answers each caller, from a stand-in for `hok waiting` of host-ctl:
python3 test_hok.py
"""

import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import daemon  # noqa: E402

LISTED = [
    {"caller": "master-x--seb", "name": "g12", "session": "s9"},
    {"caller": "ops", "name": "30+31", "session": "s1"},
    {"caller": "ops", "name": "t4", "session": ""},
    {"caller": "master-y--seb", "name": "f3", "session": ""},
]


def get_names(caller, query):
    return [row["name"] for row in daemon.list_hok(caller, query, {})[1]["requests"]]


def main():
    failures = []
    with tempfile.TemporaryDirectory() as root:
        script, count = pathlib.Path(root, "approve.py"), pathlib.Path(root, "count")
        script.write_text(
            "import pathlib, sys\n"
            f"count = pathlib.Path({str(count)!r})\n"
            "count.write_text(str(int(count.read_text() or 0) + 1) if count.exists() else '1')\n"
            f"print({json.dumps(json.dumps(LISTED))} if sys.argv[1:] == ['waiting'] else '')\n"
        )
        daemon.HOK_WAITING = script
        cases = [
            ("a bundle gets its own", get_names("master-x--seb", {}), ["g12"]),
            (
                "the host those of its ops session",
                get_names(daemon.HOST, {"session": ["s1"]}),
                ["30+31"],
            ),
            ("a bundle with none gets none", get_names("master-z--seb", {}), []),
        ]
        for name, found, expected in cases:
            if found != expected:
                failures.append(f"{name}: {found!r}, expected {expected!r}")
        if count.read_text() != "1":
            failures.append(f"hok waiting ran {count.read_text()} times in {daemon.HOK_KEPT} s")
        try:
            daemon.list_hok(daemon.HOST, {}, {})
            failures.append("the host with no session got an answer")
        except ValueError:
            pass
        daemon.HOK_WAITING = pathlib.Path(root, "none.py")
        try:
            daemon.list_hok("master-x--seb", {}, {})
            failures.append("an answer with no host-ctl")
        except daemon.Refused:
            pass
    for line in failures:
        print(line)
    print(f"{6 - len(failures)}/6 cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
