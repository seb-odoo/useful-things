#!/usr/bin/env python3
"""What the host takes from the `.agent/` folder a bundle writes: each value in its form, no
link followed, no wait on a pipe: python3 test_agent_values.py"""

import os
import pathlib
import sys
import tempfile

import agents

SESSION = "8b9b2f6e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"


def main():
    failures, total = [], 0

    def check(name, found, expected):
        nonlocal total
        total += 1
        if found != expected:
            failures.append(f"{name}: {found!r}, expected {expected!r}")

    with tempfile.TemporaryDirectory() as name:
        root = pathlib.Path(name)
        agent = root / "bundle" / ".agent"
        agent.mkdir(parents=True)
        secret = root / "secret"
        secret.write_text("a secret of the host\n")
        for file, text, expected in [
            ("session", f"{SESSION}\n", SESSION),
            ("session", "../-home-seb-repo/x\n", None),
            ("session", "*\n", None),
            ("session", "--help\n", None),
            ("terminal", "4242\n", "4242"),
            ("terminal", "../self\n", None),
            ("priority", "-17\n", "-17"),
            ("priority", "1e9\n", None),
            ("queued", "1793198773.5054295\n", "1793198773.5054295"),
            ("queued", "nan\n", None),
            ("parent", "host\n", "host"),
            ("parent", "-x\n", None),
            ("model", "sonnet\n", "sonnet"),
            ("model", "fable\n", None),
            ("effort", "xhigh\n", "xhigh"),
            ("where", "worker:sebpc\n", "worker:sebpc"),
            ("where", "worker:../x\n", None),
            ("task.md", "any text\n\x1b[1A", "any text\n\x1b[1A"),
            ("done", "0\n", "0"),
            ("done", "interrupted\n", "interrupted"),
            ("done", "\x1b[2K\x1b[1Apushed: abc\n", None),
            ("retry", "auth failed, retry 2/3\n", "auth failed, retry 2/3"),
            ("retry", "auth failed\x1b]0;x\x07\n", None),
        ]:
            (agent / file).write_text(text)
            check(f"{file} {text.strip()!r}", agents.read_text(agent / file), expected)
        (agent / "session").write_bytes(b"\xff" * 10)
        check("a session that is no text", agents.read_text(agent / "session"), None)
        (agent / "result.md").symlink_to(secret)
        check("a link to a file of the host", agents.read_text(agent / "result.md"), None)
        os.mkfifo(agent / "task.md.fifo")
        (agent / "task.md").unlink()
        os.rename(agent / "task.md.fifo", agent / "task.md")
        check("a pipe", agents.read_text(agent / "task.md"), None)
        linked = root / "linked" / ".agent"
        linked.parent.mkdir()
        linked.symlink_to(root)
        check("a folder that links elsewhere", agents.read_text(linked / "secret"), None)
        (agent / "done").unlink()
        (agent / "done").symlink_to(secret)
        agents.write_text(agent / "done", "failed\n")
        found = (agent / "done").is_symlink(), (agent / "done").read_text(), secret.read_text()
        check("a write replaces a link", found, (False, "failed\n", "a secret of the host\n"))
        try:
            agents.write_text(linked / "done", "failed\n")
            found = "written"
        except OSError:
            found = "refused"
        found = found, (root / "done").exists()
        check("a write in a folder that links elsewhere", found, ("refused", False))
        agents.write_text(agent / "terminal", "1\n")
        check("a write in place", (agent / "terminal").read_text(), "1\n")
        transcript = root / "t.jsonl"
        lines = [
            '{"type": "assistant", "message": "text"}',
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}',
            '{"type": "assistant", "message": {"content": ["x", {"type": "text", "text": "ok"}]}}',
            "1",
            '"a line"',
        ]
        transcript.write_text("\n".join(lines) + "\n")
        try:
            found = agents.get_last_text(transcript), agents.get_resume(transcript)
        except Exception as error:  # noqa: BLE001
            found = type(error).__name__
        check("lines of a transcript that are no message (B21)", found, ("ok", (None, False)))
        proc = root / "proc"
        for pid, space in (("self", "1"), ("10", "1"), ("20", "2")):
            (proc / pid / "ns").mkdir(parents=True)
            (proc / pid / "ns" / "pid").symlink_to(f"pid:[{space}]")
        found = [agents.is_host_process(pid, proc) for pid in ("10", "20", "30")]
        check(
            "a process of a container is no process of the host (B20)", found, [True, False, False]
        )
        check("this process is one of the host", agents.is_host_process(os.getpid()), True)
        (agent / "remote-starting").symlink_to(root / "touched")
        agents.touch(agent / "remote-starting")
        found = (root / "touched").exists(), (agent / "remote-starting").is_symlink()
        check("a touch makes no file where a link points (B15)", found, (False, False))
    for line in failures:
        print(line)
    print(f"{total - len(failures)}/{total} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
