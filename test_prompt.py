#!/usr/bin/env python3
"""The prompt of the host shell in a git repo (.bashrc), in scratch repos: python3 test_prompt.py

git's own prompt, drawn as bash-git-prompt drew it: the folder, then [branch, behind and ahead,
state]. A `.bash-git-rc` at the top of a repo, which a bundle writes in its worktrees, runs
nothing (security check 11-03, C6).
"""

import os
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
START = "# The prompt in a git repo"
STATES = """
cd "$1" || exit 1
git init -q -b master remote.git --bare
git init -q -b master w
cd w || exit 1
echo 'X="$(touch ../sourced)"' > .bash-git-rc
echo a > a
git add a .bash-git-rc
git commit -qm a
git remote add origin ../remote.git
git push -q -u origin master
show() { __prompt_git; printf '%s\\n' "$1: ${PS1@P}"; }
show clean
echo b >> a; show changed
git add a; show staged
echo c > u; show untracked
git commit -qm b; show ahead
git push -q; git reset -q --hard HEAD~1; rm u; show behind
git checkout -q -b 'x$(touch${IFS}${HOME}/injected)'; show "a branch name of code"
false; show "a failed command"
cd ..; show outside
"""
EXPECTED = [
    "clean: \u2714  HH:MM ~/w [ master | \u2714  ] $ ",
    "changed: \u2714  HH:MM ~/w [ master | \u271a ] $ ",
    "staged: \u2714  HH:MM ~/w [ master | \u25cf ] $ ",
    "untracked: \u2714  HH:MM ~/w [ master | \u25cf \u2026 ] $ ",
    "ahead: \u2714  HH:MM ~/w [ master \u2191\u00b71 | \u2026 ] $ ",
    "behind: \u2714  HH:MM ~/w [ master \u2193\u00b71 | \u2714  ] $ ",
    "a branch name of code: \u2714  HH:MM ~/w [ x$(touch${IFS}${HOME}/injected) | \u2714  ] $ ",
    "a failed command: \u2718  HH:MM ~/w [ x$(touch${IFS}${HOME}/injected) | \u2714  ] $ ",
    "outside: plain $ ",
]


def main():
    text = (HERE / ".bashrc").read_text()
    block = text[text.index(START) :]
    block = block[: block.index("\nfi\n") + 4]
    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(root),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "me",
            "GIT_AUTHOR_EMAIL": "me@localhost",
            "GIT_COMMITTER_NAME": "me",
            "GIT_COMMITTER_EMAIL": "me@localhost",
        }
        script = f"PS1='plain $ '\n{block}\n{STATES}"
        res = subprocess.run(
            ["bash", "--norc", "-c", script, "_", str(root)],
            capture_output=True,
            check=False,
            env=env,
            text=True,
        )
        found = [
            line.replace("\x01", "").replace("\x02", "")
            for line in res.stdout.splitlines()
        ]
        found = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in found]
        found = [re.sub(r"\b[0-9]{2}:[0-9]{2}\b", "HH:MM", line) for line in found]
        failures = [f"{line!r}, expected {want!r}" for line, want in zip(found, EXPECTED) if line != want]
        if len(found) != len(EXPECTED):
            failures.append(f"{len(found)} prompts, expected {len(EXPECTED)}: {res.stderr.strip()}")
        if (root / "sourced").exists():
            failures.append("the .bash-git-rc of the repo ran")
        if (root / "injected").exists():
            failures.append("the name of a branch ran")
    for line in failures:
        print(line)
    print(f"{len(EXPECTED) + 2 - len(failures)}/{len(EXPECTED) + 2} cases as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
