#!/usr/bin/env python3
"""What a worker answers and what a bundle relays to it, without a worker: the status the daemon
keeps, and the sessions of the one connection a bundle takes: python3 test_workers.py
"""

import json
import pathlib
import socket
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import workers

MAX_ANSWER = getattr(workers, "MAX_ANSWER", 64 * 1024)
MAX_RELAYS = getattr(workers, "MAX_RELAYS", 4)

STATUSES = [
    ({"gigabytes_free": 9, "agents_free": 3, "wait": 0}, True),
    ({"gigabytes_free": "9", "agents_free": 3}, False),
    ({"wait": "0"}, False),
    ({"agents_free": None}, False),
    ({"gigabytes_free": float("nan")}, False),
    ({"agents_free": True}, False),
    ({"agent_wait": "the memory of the VM is short"}, True),
    ({"agent_wait": "\x1b[2Kfull"}, False),
    ({"agent_wait": ["x"]}, False),
    ({"error": "no"}, False),
    ([1, 2], False),
    ({"wait": 10**400}, False),
]


class Handler:
    """The side of the daemon of a bundle's connection: a socket pair."""

    def __init__(self):
        self.connection, self.peer = socket.socketpair()
        self.rfile = self.connection.makefile("rb")
        self.wfile = self.connection.makefile("wb")

    def send_response(self, code):
        pass

    def send_header(self, name, value):
        pass

    def end_headers(self):
        pass


def get_workers(command):
    found = workers.Workers(["sebpc"])
    found.is_up = lambda name: True
    found.ssh = lambda name, *words: command
    return found


def check_statuses(failures):
    """A status whose numbers are no numbers is not kept: it stopped every start of the laptop
    (security check 11-03, G3). Neither is an answer past MAX_ANSWER or STATUS_TIMEOUT."""
    for status, kept in STATUSES:
        found = get_workers([sys.executable, "-c", f"print({json.dumps(json.dumps(status))})"])
        found.poll()
        if ("sebpc" in found.status) != kept:
            failures.append(f"status {status!r}: kept {not kept}")
        try:
            found.choose("odoo", 1), found.choose_agent()
        except Exception as error:  # noqa: BLE001
            failures.append(f"status {status!r}: {type(error).__name__} {error}")
    big = '{"wait": 0, "x": "' + "a" * (MAX_ANSWER + 10) + '"}'
    found = get_workers([sys.executable, "-c", f"print({big!r})"])
    found.poll()
    if "sebpc" in found.status:
        failures.append("an answer over MAX_ANSWER is kept")
    found = get_workers(["sleep", "30"])
    timeout, workers.STATUS_TIMEOUT = getattr(workers, "STATUS_TIMEOUT", 20), 1
    started = time.monotonic()
    try:
        found.poll()
    finally:
        workers.STATUS_TIMEOUT = timeout
    if "sebpc" in found.status or time.monotonic() - started > 5:
        failures.append("an answer that does not come is waited for")
    return len(STATUSES) + 2


def check_polling(failures):
    """A poll that raises leaves the next one to come: the thread that asks the workers ended
    for good on one status the checks did not foresee (security check 11-03, G3)."""
    found = workers.Workers(["sebpc"])
    calls = []

    def poll():
        calls.append(1)
        raise OverflowError("a count of 401 digits")

    found.poll = poll
    every, workers.POLL = workers.POLL, 0.05
    thread = threading.Thread(target=found.keep_polling, daemon=True)
    try:
        thread.start()
        time.sleep(0.5)
    finally:
        workers.POLL = every
    if not thread.is_alive() or len(calls) < 2:
        failures.append(f"polling: alive {thread.is_alive()} after {len(calls)} polls")
    return 1


def check_follows(failures):
    """A bundle follows MAX_FOLLOWS jobs at once, counted by their ssh (security check 11-03,
    B23)."""
    with tempfile.TemporaryDirectory() as name:
        proc = pathlib.Path(name)
        follow = ["ssh", "-S", "m", "-T", "worker", "follow", "b1", "7"]
        (proc / "self" / "ns").mkdir(parents=True)
        (proc / "self" / "ns" / "pid").symlink_to("pid:[1]")
        for pid, (args, space) in enumerate(
            [
                (follow, 1),
                (follow, 1),
                (follow, 2),
                (["ssh", "worker", "follow", "b2", "7"], 1),
                (["sh", "worker", "follow", "b1"], 1),
            ]
        ):
            (proc / str(pid) / "ns").mkdir(parents=True)
            (proc / str(pid) / "ns" / "pid").symlink_to(f"pid:[{space}]")
            (proc / str(pid) / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in args))
        counted = getattr(workers, "count_follows", lambda bundle, proc: 0)("b1", proc)
    found = get_workers(["true"])
    counter = getattr(workers, "count_follows", None)
    workers.count_follows = lambda bundle: getattr(workers, "MAX_FOLLOWS", 5)
    try:
        found.relay(Handler(), "b1", "sebpc", "follow", ["7"])
        refused = False
    except workers.Refused:
        refused = True
    finally:
        if counter:
            workers.count_follows = counter
    found = get_workers(["true"])
    found.stream = lambda *args: time.sleep(0.3)
    workers.count_follows = lambda bundle: 0
    passed = []

    def follow():
        try:
            found.relay(Handler(), "b1", "sebpc", "follow", ["7"])
            passed.append(True)
        except workers.Refused:
            pass

    threads = [threading.Thread(target=follow) for _ in range(20)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
    finally:
        if counter:
            workers.count_follows = counter
    found = (counted, refused, len(passed))
    if found != (2, True, getattr(workers, "MAX_FOLLOWS", 5)):
        failures.append(f"follows: counted, refused, let through at once: {found}")
    return 1


def check_relays(failures):
    """A bundle opens MAX_RELAYS streams at most, an upload that sends nothing ends after
    UPLOAD_IDLE, and its place is given back (security check 11-03, B23)."""
    found = get_workers(["cat"])
    idle, workers.UPLOAD_IDLE = getattr(workers, "UPLOAD_IDLE", 300), 1
    handlers = [Handler() for _ in range(MAX_RELAYS)]
    codes = []
    threads = [
        threading.Thread(
            target=lambda handler=handler: codes.append(
                found.relay(handler, "b1", "sebpc", "submit", [])
            ),
            daemon=True,
        )
        for handler in handlers
    ]
    try:
        for thread in threads:
            thread.start()
        time.sleep(0.3)
        checks = [("all are open", getattr(found, "relays", {}).get("b1") == MAX_RELAYS)]
        refused = []

        def one_more():
            try:
                found.relay(Handler(), "b1", "sebpc", "submit", [])
            except workers.Refused as error:
                refused.append(str(error))

        thread = threading.Thread(target=one_more, daemon=True)
        thread.start()
        thread.join(5)
        checks.append(("one more is refused", "streams to the workers" in "".join(refused)))
        other = Handler()
        other.peer.sendall(b"x")
        other.peer.shutdown(socket.SHUT_WR)
        code = found.relay(other, "b2", "sebpc", "submit", [])
        checks.append(("another bundle still relays", code == 0 and other.peer.recv(10) == b"x"))
        for thread in threads:
            thread.join(5)
        checks.append(("the idle uploads end", not any(thread.is_alive() for thread in threads)))
        checks.append(
            ("their places are back", getattr(found, "relays", None) == {} and len(codes) == 4)
        )
    finally:
        workers.UPLOAD_IDLE = idle
    failures.extend(f"relays: {name}" for name, passed in checks if not passed)
    return len(checks)


def main():
    failures = []
    count = (
        check_statuses(failures)
        + check_polling(failures)
        + check_follows(failures)
        + check_relays(failures)
    )
    for line in failures:
        print(line)
    print(f"{count - len(failures)}/{count} cases as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
