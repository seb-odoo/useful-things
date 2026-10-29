#!/usr/bin/env python3
"""The testdb names of the container bashrc, on bundles of every length: python3 test_testdb_names.py"""

import itertools
import os
import subprocess
import sys
import tempfile
import types

import commands
import delete_bundle

BASHRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../container-rw/devcontainer.bashrc")
PURPOSES = ("", "hoot", "hoot-18.0", "hoot-saas-19.4", "mock-store-parity", "e", "t-d")


def testdb(bundle, purpose):
    script = f'source "{BASHRC}"; branchdb() {{ echo "$0"; }}; testdb "$1"'
    res = subprocess.run(
        ["bash", "-c", script, bundle, purpose],
        capture_output=True,
        check=False,
        env={**os.environ, "BUNDLE_SUFFIX": commands.BUNDLE_SUFFIX},
        text=True,
    )
    return res.stdout.strip() if res.returncode == 0 else None


def main():
    suffix = commands.BUNDLE_SUFFIX
    bundles = [f"master-{'a' * n}{suffix}" for n in range(3, 75)]
    bundles += [f"saas-19.2-{'b' * n}{suffix}-578779-fw" for n in range(3, 75)]
    problems = []
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as worktrees:
        delete_bundle.FILESTORE_CONTAINER = root
        delete_bundle.WORKTREE_CONTAINER = worktrees
        for bundle in bundles:
            os.makedirs(f"{worktrees}/base/{bundle.replace(suffix, f'-bar{suffix}', 1)}/odoo")

        def seb_names(bundle):
            return {
                bundle + "".join(f"-{letter}" for letter in letters)
                for size in range(4)
                for letters in itertools.combinations("etd", size)
            }

        for bundle in bundles:
            names = {purpose: testdb(bundle, purpose) for purpose in PURPOSES}
            longer = bundle.replace(suffix, f"-bar{suffix}", 1)
            of_longer = seb_names(longer) | {testdb(longer, purpose) for purpose in PURPOSES}
            out = types.SimpleNamespace(
                stdout="\n".join(filter(None, [*names.values(), *of_longer]))
            )
            runner = types.SimpleNamespace(run=lambda *args, out=out, **kwargs: out)
            found = set(delete_bundle._bundle_databases(runner, bundle))
            for name in sorted(found & of_longer):
                problems.append(f"{bundle}: delete_bundle takes {name} of {longer}")
            seb = seb_names(bundle)
            bug_fix = {f"{bundle}-claude-bug", f"{bundle}-claude-fix", f"{bundle}-bg", f"{bundle}-fx"}
            for purpose, name in names.items():
                if name is None:
                    problems.append(f"{bundle} {purpose!r}: refused")
                elif len(name) > 63:
                    problems.append(f"{bundle} {purpose!r}: {name} is over 63 chars")
                elif name not in found:
                    problems.append(f"{bundle} {purpose!r}: delete_bundle misses {name}")
                elif name in seb | bug_fix:
                    problems.append(f"{bundle} {purpose!r}: {name} is a DB of Seb or of bug-fix.sh")
            if len(set(names.values())) != len(names):
                problems.append(f"{bundle}: two purposes share a name: {names}")
            for purpose in ("bug", "fix"):
                if testdb(bundle, purpose) is not None:
                    problems.append(f"{bundle} {purpose!r}: bug-fix.sh's purpose accepted")
    print("\n".join(problems))
    print(f"{len(bundles)} bundles of {len(bundles[0])} to {len(bundles[-1])} chars: {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
