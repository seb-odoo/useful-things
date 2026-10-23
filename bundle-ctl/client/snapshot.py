"""The state of a worktree, sent to a worker as a git bundle that holds only what it lacks.

A snapshot is a commit of the worktree as it is, tracked and untracked files alike, made with a
copy of the index and objects of its own: the shared `.git` and the index are never written. Its
first parent is the upstream tree it starts from, its second the previous snapshot of the bundle,
when the worker has them: the bundle then carries only the files that differ.

The worker keeps "tree commits", the same parentless commit of a tree on any machine, so that the
requester can name one it never received.
"""

import os
import pathlib
import subprocess

FIXED = {
    "GIT_AUTHOR_NAME": "remote-runs",
    "GIT_AUTHOR_EMAIL": "remote-runs@localhost",
    "GIT_AUTHOR_DATE": "@0 +0000",
    "GIT_COMMITTER_NAME": "remote-runs",
    "GIT_COMMITTER_EMAIL": "remote-runs@localhost",
    "GIT_COMMITTER_DATE": "@0 +0000",
}
SAFE = ["-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "commit.gpgSign=false"]
MAX_UNTRACKED = 50 * 1024**2


class Refused(Exception):
    pass


class Snapshot:
    def __init__(self, worktree, cache):
        self.worktree = pathlib.Path(worktree)
        self.cache = pathlib.Path(cache)
        self.private = self.cache / "repo.git"

    def git(self, *args, private=True, env=None, binary=False):
        command = ["git", *SAFE]
        if private:
            command += ["--git-dir", str(self.private), "--work-tree", str(self.worktree)]
        else:
            command += ["-C", str(self.worktree)]
        result = subprocess.run(
            [*command, *args],
            capture_output=True,
            check=False,
            env={**os.environ, **(env or {})},
        )
        if result.returncode:
            raise Refused(f"git {args[0]}: {result.stderr.decode(errors='replace').strip()}")
        return result.stdout if binary else result.stdout.decode().strip()

    def prepare(self):
        """The private repo: its own objects, the real ones read through alternates."""
        common = pathlib.Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir",
                                       private=False))
        if not (self.private / "objects").exists():
            subprocess.run(["git", "init", "-q", "--bare", str(self.private)], check=True)
        (self.private / "objects" / "info" / "alternates").write_text(f"{common / 'objects'}\n")

    def write_tree(self):
        """The tree of the worktree as it is, through a copy of its index."""
        index = pathlib.Path(self.git("rev-parse", "--path-format=absolute", "--git-path", "index",
                                      private=False))
        copy = self.cache / "index"
        copy.write_bytes(index.read_bytes())
        untracked = self.git("ls-files", "-o", "--exclude-standard", "-z", private=False)
        size = sum(
            (self.worktree / name).stat().st_size
            for name in untracked.split("\0")
            if name and (self.worktree / name).is_file()
        )
        if size > MAX_UNTRACKED:
            raise Refused(f"{size} bytes of untracked files, over {MAX_UNTRACKED}")
        env = {"GIT_INDEX_FILE": str(copy)}
        excludes = self.git("rev-parse", "--path-format=absolute", "--git-path", "info/exclude",
                            private=False)
        self.git("-c", f"core.excludesFile={excludes}", "add", "-A", env=env)
        return self.git("write-tree", env=env)

    def tree_commit(self, tree, *parents):
        args = ["commit-tree", tree, "-m", "tree"]
        for parent in parents:
            args += ["-p", parent]
        return self.git(*args, env=FIXED)

    def has(self, commit):
        try:
            self.git("cat-file", "-e", f"{commit}^{{commit}}")
        except Refused:
            return False
        return True

    def is_ancestor(self, older, newer):
        try:
            self.git("merge-base", "--is-ancestor", older, newer)
        except Refused:
            return False
        return True

    def pick_base(self, base, trees):
        """The tree commit to start from: the one of `base` itself, else the nearest one of an
        ancestor of `base`, checked here, as the worker's list is only a hint.
        """
        usable = []
        for commit, sent in trees:
            if not self.has(commit):
                continue
            if self.tree_commit(self.git("rev-parse", f"{commit}^{{tree}}")) != sent:
                continue
            if commit == base:
                return commit, sent
            if self.is_ancestor(commit, base):
                usable.append((int(self.git("rev-list", "--count", f"{commit}..{base}")), commit,
                               sent))
        if usable:
            _, commit, sent = min(usable)
            return commit, sent
        return None, None

    def make(self, base, have):
        """The snapshot commit and the git bundle that brings it to a worker whose `have` is
        given, or no bundle when the worker already holds the same tree.
        """
        self.prepare()
        tree = self.write_tree()
        snap, worker_tree = have.get("snap"), have.get("tree")
        if snap and worker_tree == tree:
            return snap, None
        base_tree = self.git("rev-parse", f"{base}^{{tree}}")
        own = self.tree_commit(base_tree)
        known, sent = self.pick_base(base, have.get("trees", []))
        negatives = []
        if known == base:
            first = own
            negatives.append(own)
        elif known:
            first = self.tree_commit(base_tree, sent)
            negatives.append(sent)
        else:
            first = own
        parents = [first]
        if snap and self.has(snap):
            parents.append(snap)
            negatives.append(snap)
        commit = self.tree_commit(tree, *parents)
        self.git("update-ref", "refs/remote-runs/snap", commit)
        data = self.git("bundle", "create", "-", "refs/remote-runs/snap",
                        *[f"^{negative}" for negative in negatives], binary=True)
        return commit, data
