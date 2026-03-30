"""Fetch bundle info from runbot"""

import subprocess
import time

from concurrent.futures import ThreadPoolExecutor
from rich.live import Live
from rich.tree import Tree
from typing import Callable, Collection, Self


def ignore_error(runner: "Runner"):
    pass


class Runner:
    def __init__(self, *, start_time=None, cwd=None, live: Live = None, tree: Tree = None):
        self.start_time = start_time or time.perf_counter()
        self.cwd = cwd
        self.live = live
        self.tree = tree

    def with_params(self, *, cwd=None, live: Live = None, tree: Tree = None):
        return self.__class__(
            start_time=self.start_time,
            cwd=cwd if cwd is not None else self.cwd,
            live=live if live is not None else self.live,
            tree=tree if tree is not None else self.tree,
        )

    def tree_add(self, label):
        res = self.with_params(tree=self.tree.add(label))
        if res.live:
            res.live.refresh()
        return res

    def run(
        self,
        cmd,
        *,
        capture_output=True,
        cwd=None,
        handle_exceptions=None,
        **kwargs,
    ):
        runner = self
        cwd = cwd or self.cwd
        local_live = False
        if not runner.live:
            local_live = True
            runner = runner.with_params(live=Live("", auto_refresh=False))
            runner.live.start()
        try:
            run = Runner.Run(
                runner=runner,
                cmd=cmd,
                capture_output=capture_output,
                cwd=cwd,
                handle_exceptions=handle_exceptions,
                **kwargs,
            )
            return run.execute()
        finally:
            if local_live:
                runner.live.stop()

    @property
    def elapsed_time(self):
        return time.perf_counter() - self.start_time

    class Run:
        def __init__(self, *, runner, cmd, handle_exceptions, **kwargs):
            self.runner = runner
            self.cmd = cmd
            self.kwargs = kwargs
            self.starting_print = (
                f"{self.runner.elapsed_time:.2f} [yellow]From: "
                + (kwargs.get("cwd") or "current folder")
                + "[/yellow]"
            )
            self.caught = None
            self.handle_exceptions = handle_exceptions
            if self.runner.tree:
                self.runner = self.runner.tree_add("")

        def execute(self):
            error = None
            try:
                self._update_state("⏳️")
                res = subprocess.run(self.cmd, check=True, text=True, **self.kwargs)
                self._update_state("✅️")
                return res
            except subprocess.CalledProcessError as e:
                if self.handle_exceptions:
                    for msg, handler in self.handle_exceptions.items():
                        if msg in e.stderr:
                            self.caught = msg
                            self._update_state("❎️")
                            handler(self.runner)
                            return
                self._update_state("❌")
                error = e.stderr
                raise
            finally:
                if error:
                    if self.runner.tree:
                        self.runner.tree.add(error)
                        self.runner.live.refresh()
                    else:
                        self.runner.live.console.print(error)
                        self.runner.live.refresh()

        def _print_progress(self):
            text = f"{self.starting_print}\n{self.runner.elapsed_time:.2f} {self.state} {' '.join(self.cmd)}"
            if self.caught:
                text += f"\n{self.caught}"
            if not self.runner.tree:
                self.runner.live.update(text, refresh=True)
                return
            self.runner.tree.label = text
            self.runner.live.refresh()

        def _update_state(self, state):
            self.state = state
            self._print_progress()

    def parallel_run[T](
        self,
        tree: Tree,
        collection: Collection[T],
        func: Callable[[Self, T], None],
        key: Callable[[T], str] = None,
    ):
        if not collection:
            return
        runner = self.with_params(tree=tree)
        local_live = False
        if not runner.live:
            local_live = True
            runner = runner.with_params(live=Live(runner.tree, auto_refresh=False))
            runner.live.start()
        try:
            futures = []
            with ThreadPoolExecutor(max_workers=len(collection)) as executor:
                for item in collection:
                    futures.append(
                        executor.submit(
                            func,
                            runner.tree_add(key(item) if key else item),
                            item,
                        )
                    )
            for future in futures:
                future.result()
        finally:
            runner.live.refresh()
            if local_live:
                runner.live.stop()
