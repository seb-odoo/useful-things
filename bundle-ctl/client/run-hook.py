#!/usr/bin/env python3
"""PreToolUse hook (Bash) of the bundles: start a heavy command through the run queue of the host.

A command that runs Odoo (a test run, an install, a server, a shell) or test-warden gets one line
in front of it, `bctl.py run`, which waits for cores and pins the shell of the call to them. The
agent writes its command as before, and bundle-ctl decides when it runs (README.md, "Runs"). The
line lets the command go on when the daemon does not answer.

    echo '{"tool_input": {"command": "./odoo-bin -d x --test-tags /mail"}}' | python3 run-hook.py
"""

import json
import os
import pathlib
import re
import shlex
import sys

ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)
CLIENT = pathlib.Path(__file__).resolve().parent / "bctl.py"
DEFAULT_WORKERS = 4
DESCRIPTOR = re.compile(r"(?<=\s)\d(?=>)")
FALLBACK = (
    ("hoot", r"engine-linux-\w+\s+-|test-warden\.cjs\s"),
    ("odoo", r"odoo-bin\s[^\n;|&]*--(?:stop-after-init|test-file|test-tags)"),
    ("server", r"odoo-bin\s+-"),
)
HELP = {"--help", "--version", "-h"}
KEYWORDS = {"!", "do", "elif", "else", "if", "then", "until", "while", "{"}
LABEL = 120
LEFT_ALONE = re.compile(r"git\s+(?:reset\s+--hard|checkout\s+--|restore\b)|\bbctl(?:\.py)?\s+run\b")
MAX_DEPTH = 2
MAX_SCRIPT = 256 * 1024
MAX_WORKERS = 32
ODOO_COMMANDS = {"populate": "odoo", "server": "server", "shell": "shell", "start": "server"}
ODOO_RUN = {
    "--init",
    "--stop-after-init",
    "--test-enable",
    "--test-file",
    "--test-tags",
    "--update",
    "-i",
    "-u",
}
OPERAND = re.compile(r"-.*|[\d.,-]+[smhd]?")
PUNCTUATION = "();<>|&\n"
PYTHON = re.compile(r"python[\d.]*")
RANK = ("shell", "server", "odoo", "hoot")
SHELLS = {"bash", "eval", "sh"}
SOCKET = "/run/bundle-ctl/ctl.sock"
TEST_HELPERS = {"otf", "ott", "otta", "ottb"}
VARIABLE = re.compile(r"\$(?:(\w+)|\{(\w+)\})")
WORKERS = re.compile(r"--workers[ =](\d+)")
WRAPPERS = {
    "command",
    "env",
    "exec",
    "ionice",
    "nice",
    "nohup",
    "setsid",
    "stdbuf",
    "taskset",
    "time",
    "timeout",
}


def split_commands(text):
    """The simple commands of a shell text, each as its words. A `$(...)` is left out."""
    text = DESCRIPTOR.sub("", text.replace("\\\n", " "))
    lexer = shlex.shlex(text, posix=True, punctuation_chars=PUNCTUATION)
    lexer.commenters = ""
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    commands, depth, redirected = [[]], 0, False
    for token in lexer:
        if not set(token) <= set(PUNCTUATION):
            if not depth and not redirected:
                commands[-1].append(token)
            redirected = False
        elif "<" in token or ">" in token:
            redirected = True
        else:
            for char in token:
                if char == "(" and (depth or commands[-1][-1:] and commands[-1][-1].endswith("$")):
                    depth += 1
                elif char == ")" and depth:
                    depth -= 1
                elif not depth:
                    commands.append([])
    return [words for words in commands if words]


def expand(words, values):
    """The words with the variables the text set before put in, split as the shell splits them."""
    expanded = []
    for word in words:
        new = VARIABLE.sub(lambda match: values.get(match[1] or match[2], match[0]), word)
        try:
            expanded += [word] if new == word else shlex.split(new)
        except ValueError:
            expanded.append(word)
    return expanded


def read_script(path, folder):
    """The text of a shell script a command runs, empty when it cannot be read."""
    path = pathlib.Path(folder, path)
    try:
        return path.read_text(errors="replace") if path.stat().st_size <= MAX_SCRIPT else ""
    except OSError:
        return ""


def strip_wrappers(words):
    words = list(words)
    while words:
        if words[0] in KEYWORDS or ASSIGNMENT.fullmatch(words[0]):
            del words[0]
        elif os.path.basename(words[0]) in WRAPPERS:
            del words[0]
            while words and (OPERAND.fullmatch(words[0]) or ASSIGNMENT.fullmatch(words[0])):
                del words[0]
        else:
            break
    return words


def get_script(args):
    """The script an interpreter runs and the arguments of that script."""
    index = next((index for index, arg in enumerate(args) if not arg.startswith("-")), None)
    return (None, []) if index is None else (args[index], args[index + 1 :])


def get_hoot(args):
    if HELP.intersection(args):
        return None
    if "--debug" in args or "--visible" in args:
        return "hoot", 1
    asked = WORKERS.search(" ".join(args))
    return "hoot", min(max(int(asked[1]), 1), MAX_WORKERS) if asked else DEFAULT_WORKERS


def get_odoo(args, letters=None):
    """The kind of an odoo-bin call. `letters` are the editions the shell helper takes first."""
    flags = {arg.partition("=")[0] for arg in args}
    if HELP & flags:
        return None
    if ODOO_RUN & flags or "p" in (letters or ""):
        return "odoo", 1
    if letters is None and args and not args[0].startswith("-"):
        kind = ODOO_COMMANDS.get(args[0])
        return kind and (kind, 1)
    return "shell" if "s" in (letters or "") else "server", 1


def get_kind(words, folder, depth):
    """The kind of run a simple command is and its workers, None when it is light."""
    words = strip_wrappers(words)
    if not words:
        return None
    program, args = words[0], words[1:]
    name = os.path.basename(program)
    if name in SHELLS or name.endswith(".sh"):
        if name == "eval":
            text = " ".join(args)
        elif name in SHELLS and "-c" in args:
            text = " ".join(args[args.index("-c") + 1 :])
        else:
            script = program if name.endswith(".sh") else get_script(args)[0]
            text = read_script(script or "", folder)
        found = depth < MAX_DEPTH and classify(text, folder, depth + 1)
        return found and found[:2]
    if program == "odoo-bin":
        letters = args[0] if args and not args[0].startswith("-") else ""
        return get_odoo(args[1:] if letters else args, letters)
    if program == "obet":
        return get_odoo(args, "et")
    if program in TEST_HELPERS:
        return "odoo", 1
    if PYTHON.fullmatch(name) or name == "node":
        program, args = get_script(args)
        name = os.path.basename(program or "")
    if name == "odoo-bin":
        return get_odoo(args)
    if name in ("test-warden-engine", "test-warden.cjs", "twc") or name.startswith("engine-linux-"):
        return get_hoot(args)
    return None


def classify(text, folder=".", depth=0):
    """The heaviest run of a shell text: its kind, its workers and the command that makes it.
    A script the text runs is read from `folder` when its path is relative.
    """
    try:
        commands = split_commands(text)
    except ValueError:
        asked = WORKERS.search(text)
        workers = int(asked[1]) if asked else DEFAULT_WORKERS
        return next(
            (
                (kind, min(workers, MAX_WORKERS) if kind == "hoot" else 1, text)
                for kind, pattern in FALLBACK
                if re.search(pattern, text)
            ),
            None,
        )
    found, values = [], {}
    for words in commands:
        words = expand(words, values)
        for word in words:
            if not ASSIGNMENT.fullmatch(word):
                break
            name, _, values[name] = word.partition("=")
        if kind := get_kind(words, folder, depth):
            found.append((*kind, " ".join(words)))
    return max(found, key=lambda run: (RANK.index(run[0]), run[1]), default=None)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not os.path.exists(SOCKET) or LEFT_ALONE.search(command):
        return
    found = classify(command, data.get("cwd") or ".")
    if not found:
        return
    kind, workers, label = found
    queue = (
        f"python3 {shlex.quote(str(CLIENT))} run --kind {kind} --workers {workers}"
        f" --label={shlex.quote(label[:LABEL])}"
    )
    answer = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": f"{kind} run, started through the run queue of the host",
            "updatedInput": {**tool_input, "command": f"{queue}\n{command}"},
        },
    }
    print(json.dumps(answer))


if __name__ == "__main__":
    main()
