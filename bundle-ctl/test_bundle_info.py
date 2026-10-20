#!/usr/bin/env python3
"""What bundle-info.py prints of the conversation of a bundle, from a fake transcript:
python3 test_bundle_info.py"""

import importlib.util
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("info", HERE / "bundle-info.py")
info = importlib.util.module_from_spec(spec)
spec.loader.exec_module(info)

SESSION = "4168f1e7-054e-45da-a35c-c3cde2ad2444"
LONG = "x" * (info.MAX_TEXT + 10)
ENTRIES = [
    {"type": "user", "timestamp": "t1", "message": {"content": "Fix the emojis"}},
    {"type": "user", "isMeta": True, "timestamp": "t1", "message": {"content": "Caveat: a note"}},
    {"type": "attachment", "timestamp": "t1"},
    {"type": "assistant", "timestamp": "t2", "message": {"content": [{"type": "thinking", "thinking": "hm"}]}},
    {"type": "assistant", "timestamp": "t3", "message": {"content": [
        {"type": "text", "text": LONG},
        {"type": "tool_use", "name": "Bash", "input": {"command": "git status\ngit log", "description": "d"}},
    ]}},
    {"type": "user", "timestamp": "t4", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
    {"type": "assistant", "isSidechain": True, "timestamp": "t5", "message": {"content": [{"type": "text", "text": "sub"}]}},
    {"type": "assistant", "timestamp": "t6", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {}}]}},
    {"type": "assistant", "timestamp": "t7", "message": {"content": [
        {"type": "text", "text": f"{LONG}\n\n**Done**\n- 3/3\n\nWaiting for you: [odoo#5](https://github.com/odoo/odoo/pull/5)"},
    ]}},
]
failures = []


def check(what, got, expected):
    if got != expected:
        failures.append(f"{what}: got {got!r}, expected {expected!r}")


def run(folder, transcript):
    info.get_agent_folder = lambda bundle: folder
    info.get_transcript = lambda session: transcript if session == SESSION else None
    return info.get_info("master-x--seb")


def main():
    with tempfile.TemporaryDirectory() as name:
        folder = pathlib.Path(name)
        transcript = folder / f"{SESSION}.jsonl"
        transcript.write_text("".join(f"{json.dumps(entry)}\n" for entry in ENTRIES) + "not json\n[1]\n")
        check("no session", run(folder, transcript), {"bundle": "master-x--seb", "close": "", "conversation": [], "session": None})
        (folder / "session").write_text(f"{SESSION}\n")
        got = run(folder, transcript)
        check("session", got["session"], SESSION)
        check("close", got["close"], "Waiting for you: odoo#5")
        check("items", [(item["role"], item["time"], item["text"][:20]) for item in got["conversation"]], [
            ("user", "t1", "Fix the emojis"),
            ("assistant", "t3", "x" * 20),
            ("tool", "t3", "Bash: git status"),
            ("tool", "t6", "Edit: "),
            ("assistant", "t7", "x" * 20),
        ])
        check("a long text is cut", got["conversation"][1]["text"], f"{'x' * info.MAX_TEXT}...")
        check("not the last answer", got["conversation"][-1]["text"].endswith("pull/5)"), True)
        info.MAX_ITEMS = 2
        check("the last items", [item["time"] for item in run(folder, transcript)["conversation"]], ["t6", "t7"])
        info.TAIL = len(json.dumps(ENTRIES[-1])) + 20
        check("a cut first line is dropped", [item["time"] for item in run(folder, transcript)["conversation"]], ["t7"])
    for line in failures:
        print(line)
    print("all as expected" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
