#!/usr/bin/env python3
"""Print the window title of a bundle: what its work is about, then its name.

    window-title.py BUNDLE

agent-terminator.py names its window with it on the host, and claude-autoopen its VS Code window in
the container. The kind comes from the name alone, so a window opened by hand gets it too. The own
suffix is BUNDLE_SUFFIX, which the containers have, else `--$USER`, the default of config.py.
"""

import os
import re
import sys

KINDS = (
    ("⏩ fw", lambda name, own: name.endswith("-fw")),
    ("📝 review", lambda name, own: not own),
    ("🤖 runbot", lambda name, own: re.search(r"-runbot-\d+$|-flake$", name)),
    ("🎯 task", lambda name, own: re.search(r"-\d{7,}$", name)),
    ("🧠 analysis", lambda name, own: name.endswith("-code-analysis")),
    ("⭐ mine", lambda name, own: True),
)


def get_title(bundle):
    suffix = os.environ.get("BUNDLE_SUFFIX") or f"--{os.environ.get('USER', 'odoo')}"
    own = bundle.endswith(suffix)
    name = bundle.removesuffix(suffix)
    kind = next(kind for kind, matches in KINDS if matches(name, own))
    return f"{kind} {bundle}"


if __name__ == "__main__":
    print(get_title(sys.argv[1]))
