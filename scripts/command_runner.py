"""Fetch bundle info from runbot"""

import subprocess
import time

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from rich.live import Live
from rich.tree import Tree


class PrintParams:
    def __init__(self, live: Live = None, tree: Tree = None):
        self.live = live
        self.tree = tree

    def tree_add(self, label):
        res = PrintParams(self.live, self.tree.add(label))
        res.live.refresh()
        return res


def ignore_error(print_params: PrintParams):
    pass


class Runner:
    def __init__(self, start_time=None):
        self.start_time = start_time or time.perf_counter()

    def run(
        self,
        cmd,
        *,
        print_params: PrintParams = None,
        capture_output=True,
        cwd=None,
        handle_exceptions=None,
        **kwargs,
    ):
        live = print_params and print_params.live
        local_live = None
        if not live:
            local_live = Live("", auto_refresh=False)
            local_live.start()
        try:
            run = Runner.Run(
                runner=self,
                cmd=cmd,
                live=live or local_live,
                tree=(print_params and print_params.tree),
                capture_output=capture_output,
                cwd=cwd,
                handle_exceptions=handle_exceptions,
                **kwargs,
            )
            run.execute()
        finally:
            if local_live:
                local_live.stop()

    @property
    def elapsed_time(self):
        return time.perf_counter() - self.start_time

    class Run:
        def __init__(self, *, runner, cmd, live: Live, tree: Tree, handle_exceptions, **kwargs):
            self.runner = runner
            self.cmd = cmd
            self.kwargs = kwargs
            self.starting_print = (
                f"{self.runner.elapsed_time:.2f} [yellow]From: "
                + (kwargs.get("cwd") or "current folder")
                + "[/yellow]"
            )
            self.live = live
            self.tree = None
            self.caught = None
            self.handle_exceptions = handle_exceptions
            if tree:
                self.tree = tree.add("")
                self.live.refresh()

        def execute(self):
            error = None
            try:
                self._update_state("⏳️")
                subprocess.run(self.cmd, check=True, text=True, **self.kwargs)
                self._update_state("✅️")
            except subprocess.CalledProcessError as e:
                if self.handle_exceptions:
                    for msg, handler in self.handle_exceptions.items():
                        if msg in e.stderr:
                            self.caught = msg
                            self._update_state("❎️")
                            handler(print_params=PrintParams(self.live, self.tree))
                            return
                self._update_state("❌")
                error = e.stderr
                raise
            finally:
                if error:
                    if self.tree:
                        self.tree.add(error)
                        self.live.refresh()
                    else:
                        self.live.console.print(error)
                        self.live.refresh()

        def _print_progress(self):
            text = f"{self.starting_print}\n{self.runner.elapsed_time:.2f} {self.state} {' '.join(self.cmd)}"
            if self.caught:
                text += f"\n{self.caught}"
            if not self.tree:
                self.live.update(text, refresh=True)
                return
            self.tree.label = text
            self.live.refresh()

        def _update_state(self, state):
            self.state = state
            self._print_progress()


@contextmanager
def live_task_executor(tree, max_workers=12):
    with Live(tree, auto_refresh=False) as live:
        print_params = PrintParams(live, tree)
        futures = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            yield lambda func, item: futures.append(executor.submit(func, item, print_params))
        for future in futures:
            future.result()
        live.refresh()
