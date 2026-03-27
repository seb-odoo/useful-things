"""Fetch bundle info from runbot"""

import subprocess
import time

from rich.live import Live
from rich.tree import Tree


def ignore_error(live, tree):
    pass


class PrintParams:
    def __init__(self, live: Live = None, tree: Tree = None):
        self.live = live
        self.tree = tree


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
            self.handle_exceptions = handle_exceptions
            if tree:
                self.tree = tree.add("")
                self.live.refresh()

        def execute(self):
            error = None
            try:
                self.state = "⏳️"
                self._print_progress()
                subprocess.run(self.cmd, check=True, text=True, **self.kwargs)
                self.state = "✅️"
            except subprocess.CalledProcessError as e:
                if self.handle_exceptions:
                    for msg, handler in self.handle_exceptions.items():
                        if msg in e.stderr:
                            handler(self.live, self.tree)
                            self.state = "❎️"
                            return
                self.state = "❌"
                error = e.stderr
                raise
            finally:
                self._print_progress()
                if error:
                    self.live.console.print(error)

        def _print_progress(self):
            text = f"{self.starting_print}\n{self.runner.elapsed_time:.2f} {self.state} {' '.join(self.cmd)}"
            if not self.tree:
                self.live.update(text, refresh=True)
                return
            self.tree.label = text
            self.live.refresh()
