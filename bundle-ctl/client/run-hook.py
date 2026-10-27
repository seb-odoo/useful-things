#!/usr/bin/env python3
"""PreToolUse hook (Bash) of the bundles: start a heavy command through the run queue of the host.

A command that runs Odoo (a test run, an install, a server, a shell) or test-warden gets one line
in front of it, `bctl.py run`, which waits for cores and pins the shell of the call to them. The
agent writes its command as before, and bundle-ctl decides when it runs (README.md, "Runs"). The
line lets the command go on when the daemon does not answer.

While the daemon waits for the turn of the agent to end before it closes its window (`bctl
requeue`), it refuses every command, on a reason that tells the agent to end its turn.

    echo '{"tool_input": {"command": "./odoo-bin -d x --test-tags /mail"}}' | python3 run-hook.py
"""

import json
import os
import pathlib
import re
import shlex
import sys
import time

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
INTERACTIVE = {"debug", "pause-before-exit", "visible"}
LOCAL_SERVER = re.compile(r"http://(?:localhost|127\.0\.0\.1):\S")
HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([^\s;&|<>()'\"]+))")
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
PAUSE = pathlib.Path("/workspace/.agent/pause")
PAUSE_MARGIN = 60
PUNCTUATION = "();<>|&\n"
PYTHON = re.compile(r"python[\d.]*")
SAFE_WORD = re.compile(r"[\w./-]+")
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


def cut_heredocs(text):
    """The text without the bodies of its heredocs, and these bodies in order. A heredoc that never
    ends keeps its lines in the text, and its body is None.
    """
    kept, bodies, ends, quote, index = [], [], [], "", 0
    while index < len(text):
        char = text[index]
        step = 2 if char == "\\" and quote != "'" else 1
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif text.startswith("<<<", index):
            step = 3
        elif match := HEREDOC.match(text, index):
            ends.append((match[1], next(end for end in match.groups()[1:] if end is not None)))
            step = match.end() - index
        elif char == "\n" and ends:
            kept.append(char)
            index += 1
            for tabs, end in ends:
                tabs = r"\t*" if tabs else ""
                match = re.compile(rf"^{tabs}{re.escape(end)}$", re.MULTILINE).search(text, index)
                bodies.append(match and text[index : match.start()])
                index = match.end() + 1 if match else index
            ends = []
            continue
        kept.append(text[index : index + step])
        index += step
    return "".join(kept), bodies


def split_commands(text):
    """The simple commands of a shell text, each as its words, the heredoc it reads, None when it
    reads none, and the files it writes. A `$(...)` is left out. A command after a `|` reads the
    heredoc of the one before.
    """
    text, bodies = cut_heredocs(text)
    text = DESCRIPTOR.sub("", text.replace("\\\n", " "))
    lexer = shlex.shlex(text, posix=True, punctuation_chars=PUNCTUATION)
    lexer.commenters = ""
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    commands, depth, redirect, bodies = [[[], None, []]], 0, "", iter(bodies)
    for token in lexer:
        if not set(token) <= set(PUNCTUATION):
            if not depth and (not redirect or ">" in redirect):
                commands[-1][2 if redirect else 0].append(token)
            redirect = ""
        elif "<" in token or ">" in token:
            redirect = token
            body = next(bodies, None) if "<<" in token and "<<<" not in token else None
            if body is not None and not depth:
                commands[-1][1] = body
        else:
            for char in token:
                words = commands[-1][0]
                if char == "(" and (depth or words[-1:] and words[-1].endswith("$")):
                    depth += 1
                elif char == ")" and depth:
                    depth -= 1
                elif not depth:
                    commands.append([[], commands[-1][1] if token == "|" else None, []])
    return [command for command in commands if command[0]]


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


def get_path(path, folder):
    return os.path.normpath(os.path.join(folder, os.path.expanduser(os.path.expandvars(path))))


def read_script(path, folder, written):
    """The text of a shell script a command runs, empty when it cannot be read. A script that a
    heredoc of the same text wrote is that heredoc: the hook runs before the file exists.
    """
    path = pathlib.Path(get_path(path, folder))
    if str(path) in written:
        return written[str(path)]
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


def get_kind(words, folder, depth, stdin, written):
    """The kind of run a simple command is and its workers, None when it is light. `stdin` is the
    heredoc it reads, and `written` the scripts the text wrote before it.
    """
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
            if script in args and "-s" in args[: args.index(script)]:
                script = None
            text = read_script(script, folder, written) if script else stdin or ""
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


def find_runs(text, folder=".", depth=0):
    """Every run of a shell text: its kind, its workers and the words of the command that makes
    it, or None when the text does not split. A script the text runs is read from `folder` when
    its path is relative.
    """
    try:
        commands = split_commands(text)
    except ValueError:
        return None
    found, values, written = [], {}, {}
    for words, stdin, outputs in commands:
        words = expand(words, values)
        for word in words:
            if not ASSIGNMENT.fullmatch(word):
                break
            name, _, values[name] = word.partition("=")
        if kind := get_kind(words, folder, depth, stdin, written):
            found.append((*kind, words))
        if stdin is not None:
            program = strip_wrappers(words)
            if program and os.path.basename(program[0]) == "tee":
                outputs += [word for word in program[1:] if not word.startswith("-")]
            written.update((get_path(path, folder), stdin) for path in expand(outputs, values))
    return found


def classify(text, folder=".", depth=0):
    """The heaviest run of a shell text: its kind, its workers and the command that makes it."""
    found = find_runs(text, folder, depth)
    if found is None:
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
    run = max(found, key=lambda run: (RANK.index(run[0]), run[1]), default=None)
    return run and (run[0], run[1], " ".join(run[2]))


def is_movable(kind, words):
    """A lone `odoo-bin` call, alone or behind its python, that stops after init on a DB it
    names, or a run of the TestWarden engine against a server of the bundle, never interactive.
    """
    flags = {word.partition("=")[0] for word in words}
    if kind == "hoot":
        name = os.path.basename(words[0])
        engine = name == "test-warden-engine" or name.startswith("engine-linux-")
        servers = [
            value if equal else "".join(words[index + 1 : index + 2])
            for index, (flag, equal, value) in enumerate(word.partition("=") for word in words)
            if flag in ("--server", "-server")
        ]
        local = len(servers) == 1 and bool(LOCAL_SERVER.match(servers[0]))
        return engine and local and not INTERACTIVE & {flag.lstrip("-") for flag in flags}
    program = words[1:2] if PYTHON.fullmatch(os.path.basename(words[0])) else words[:1]
    if kind != "odoo" or not program or os.path.basename(program[0]) != "odoo-bin":
        return False
    return "--stop-after-init" in flags and bool({"-d", "--database"} & flags)


def wrap(text, found):
    """The text with its one movable run (`is_movable`) started by `bctl.py run --route`, which
    may send it to a remote worker (README.md, "Remote runs"), or None. Its program is written
    once in the text.
    """
    if not found or len(found) != 1:
        return None
    kind, workers, words = found[0]
    words = strip_wrappers(words)
    if not words or not is_movable(kind, words):
        return None
    name = "odoo-bin" if kind == "odoo" else os.path.basename(words[0])
    starts = list(re.finditer(rf"(?:^|(?<=[\s;&|(])){re.escape(words[0])}(?=\s)", text))
    if not SAFE_WORD.fullmatch(words[0]) or len(starts) != 1 or text.count(name) != 1:
        return None
    route = f"python3 {CLIENT} run --route --kind {kind} --workers {workers} -- "
    return text[: starts[0].start()] + route + text[starts[0].start() :]


def get_pause():
    """The seconds left before the daemon closes the agent window, None when it waits for no turn.
    A file the daemon left behind counts for PAUSE_MARGIN seconds past its time."""
    try:
        left = float(PAUSE.read_text()) - time.time()
    except (OSError, ValueError):
        return None
    return max(0, round(left)) if left > -PAUSE_MARGIN else None


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    left = get_pause()
    if left is not None:
        reason = (
            f"This agent goes back to the queue: its window closes in {left} seconds, and the"
            " session goes on later from here. Run no new command, and end your turn now on where"
            " the work is and what comes next. Your files and this conversation stay as they are."
        )
        output = {"hookEventName": "PreToolUse", "permissionDecision": "deny"}
        print(json.dumps({"hookSpecificOutput": {**output, "permissionDecisionReason": reason}}))
        return
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not os.path.exists(SOCKET) or LEFT_ALONE.search(command):
        return
    found = classify(command, data.get("cwd") or ".")
    if not found:
        return
    kind, workers, label = found
    wrapped = wrap(command, find_runs(command, data.get("cwd") or "."))
    if wrapped is None:
        queue = (
            f"python3 {shlex.quote(str(CLIENT))} run --kind {kind} --workers {workers}"
            f" --label={shlex.quote(label[:LABEL])}"
        )
        wrapped = f"{queue}\n{command}"
    answer = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": f"{kind} run, started through the run queue of the host",
            "updatedInput": {**tool_input, "command": wrapped},
        },
    }
    print(json.dumps(answer))


if __name__ == "__main__":
    main()
