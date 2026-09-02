"""Fully delete a bundle locally.

Examples:
 $ python ~/repo/useful-things/scripts/delete_bundle.py master-bundle-name-ngram
 $ python ~/repo/useful-things/scripts/delete_bundle.py master-bundle-name-ngram --also-remote
 $ python ~/repo/useful-things/scripts/delete_bundle.py master-first-ngram master-second-ngram
 $ python ~/repo/useful-things/scripts/delete_bundle.py 'saas-19.*'
"""

import argparse
import fnmatch
import glob
import os
import subprocess
import threading
from contextlib import nullcontext

import argcomplete
from command_runner import ignore_error
from commands import (
    clean_bundle_name,
    get_filestore_bundle_prefix,
    get_remote_dev_repo,
    get_repo_folder,
    get_repos,
    get_worktree_bundle_folder,
    get_worktree_bundle_repo_folder,
)
from config import WORKTREE_CONTAINER
from rich import print
from rich.tree import Tree
from utils import UtilsRunner


def _existing_bundles():
    """List the names of the bundles having a worktree folder."""
    repos = list(get_repos())
    bundles = []
    for path in glob.glob(f"{WORKTREE_CONTAINER}/*/*"):
        if any(os.path.isdir(os.path.join(path, repo)) for repo in repos):
            bundles.append(os.path.basename(path))
    return sorted(bundles)


def _bundle_name_completer(prefix, parsed_args, **kwargs):
    already_given = set(getattr(parsed_args, "name", None) or [])
    return [bundle for bundle in _existing_bundles() if bundle not in already_given]


def _unsaved_work(runner: UtilsRunner, bundle_name: str):
    """Map the repos of a bundle to the work its deletion would lose, by kind.

    A worktree is read the way `git worktree remove` reads it, so the tooling files and the
    node_modules of a bundle don't count as uncommitted. A commit counts as saved as soon as a
    remote ref holds it, the dev remote included.
    """
    unsaved = {}
    lock = threading.Lock()

    def handle_repo(runner: UtilsRunner, repo: str):
        found = {}
        folder = get_worktree_bundle_repo_folder(bundle_name, repo)
        if os.path.isdir(folder):
            res = runner.run(
                ["git", "status", "--porcelain"],
                cwd=folder,
                handle_exceptions={"fatal: not a git repository": ignore_error},
            )
            if res and res.stdout.strip():
                found["uncommitted files"] = res.stdout.strip().splitlines()
        res = runner.run(
            ["git", "log", "--format=%h %s", f"refs/heads/{bundle_name}", "--not", "--remotes"],
            cwd=get_repo_folder(repo),
            handle_exceptions={
                f"fatal: ambiguous argument 'refs/heads/{bundle_name}'": ignore_error,
            },
        )
        if res and res.stdout.strip():
            found["unpushed commits"] = res.stdout.strip().splitlines()
        if found:
            with lock:
                unsaved[repo] = found

    runner.parallel_run(Tree("Unsaved work"), get_repos(), handle_repo)
    return unsaved


def expand_bundle_names(names: list[str]):
    """Expand the `fnmatch` patterns (e.g. `saas-19.*`) against the existing bundles.

    Names without any wildcard are kept as is, so that a bundle can still be cleaned up
    when its worktree folder is already gone.
    """
    expanded = []
    for name in names:
        if not any(char in name for char in "*?["):
            expanded.append(name)
            continue
        expanded.extend(fnmatch.filter(_existing_bundles(), name))
    return list(dict.fromkeys(expanded))


def delete_bundle(
    runner: UtilsRunner,
    *,
    bundle_name: str,
    force: bool = False,
    also_remote: bool = False,
    repo_locks: dict[str, threading.Lock] | None = None,
):
    """Delete a bundle, unless one of its repos holds work the deletion would lose.

    Returns that work by repo and by kind, empty once the bundle is deleted. Nothing is touched
    when there is some: the bundle folder goes as a whole below, and the branch with it.
    """
    if not force and (unsaved := _unsaved_work(runner, bundle_name)):
        return unsaved

    def handle_repo(runner: UtilsRunner, repo: str):
        # Every step below writes into the shared repository (worktree metadata, refs):
        # when several bundles are deleted at once, they must not touch the same one
        # concurrently, git would fail to lock `packed-refs`. Repositories are still
        # handled in parallel.
        with repo_locks[repo] if repo_locks else nullcontext():
            runner = runner.with_params(cwd=get_repo_folder(repo))
            wt_bundle_repo_folder = get_worktree_bundle_repo_folder(bundle_name, repo)
            runner.run(
                ["git", "worktree", "unlock", wt_bundle_repo_folder],
                handle_exceptions={
                    f"'{wt_bundle_repo_folder}' is not locked": ignore_error,
                    f"'{wt_bundle_repo_folder}' is not a working tree": ignore_error,
                },
            )
            runner.run(
                [
                    "git",
                    "worktree",
                    "remove",
                    wt_bundle_repo_folder,
                    *(["--force"] if force else []),
                ],
                handle_exceptions={
                    f"fatal: '{wt_bundle_repo_folder}' is not a working tree": ignore_error,
                },
            )
            runner.delete_branch_and_remote_ref(
                repo=repo,
                bundle_name=bundle_name,
                handle_exceptions={
                    f"error: branch '{bundle_name}' not found": ignore_error,
                },
            )
            if also_remote:
                runner.run(
                    ["git", "push", get_remote_dev_repo(repo), "--delete", bundle_name],
                    handle_exceptions={
                        f"error: unable to delete '{bundle_name}': remote ref does not exist": ignore_error,
                    },
                )

    runner.parallel_run(Tree("Repositories"), get_repos(), handle_repo)
    runner.run(["rm", "-rf", get_worktree_bundle_folder(bundle_name)])

    def handle_file(runner: UtilsRunner, file: str):
        if not file.startswith(get_filestore_bundle_prefix(bundle_name)):
            print(f"[red]Skipping {file} as it doesn't match the expected pattern[/red]")
            return
        runner.run(["rm", "-rf", file])
        last_part = file.split("/")[-1]
        runner.run(
            ["dropdb", last_part],
            handle_exceptions={
                f'ERROR:  database "{last_part}" does not exist': ignore_error,
                f'dropdb: error: database removal failed: ERROR:  database "{last_part}" does not exist': ignore_error,
            },
        )

    runner.parallel_run(
        Tree("Files"),
        glob.glob(f"{get_filestore_bundle_prefix(bundle_name)}*"),
        handle_file,
    )
    print("[green]Done[/green]")
    return {}


def delete_bundles(
    runner: UtilsRunner,
    *,
    bundle_names: list[str],
    force: bool = False,
    also_remote: bool = False,
):
    """Delete several bundles at once, one thread per bundle.

    Returns the unsaved work of the bundles left alone, by bundle name. It is reported by the
    caller: a `parallel_run` holds the live display until it is over.
    """
    repo_locks = {repo: threading.Lock() for repo in get_repos()}
    skipped = {}
    lock = threading.Lock()

    def handle_bundle(runner: UtilsRunner, bundle_name: str):
        unsaved = delete_bundle(
            runner,
            bundle_name=bundle_name,
            force=force,
            also_remote=also_remote,
            repo_locks=repo_locks,
        )
        if unsaved:
            with lock:
                skipped[bundle_name] = unsaved

    runner.parallel_run(Tree("Bundles"), bundle_names, handle_bundle)
    return skipped


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "name",
        help="Name or pattern of the bundle(s) to delete",
        type=str,
        nargs="+",
    ).completer = _bundle_name_completer
    parser.add_argument(
        "--force",
        help="Whether to delete a bundle holding unsaved work",
        action="store_true",
    )
    parser.add_argument(
        "--also-remote",
        help="Whether to also delete the remote bundle",
        action="store_true",
    )
    argcomplete.autocomplete(parser)
    args = parser.parse_args()
    bundle_names = expand_bundle_names([clean_bundle_name(name) for name in args.name])
    if not bundle_names:
        parser.error("no bundle to delete")
    try:
        skipped = delete_bundles(
            UtilsRunner(),
            bundle_names=bundle_names,
            force=args.force,
            also_remote=args.also_remote,
        )
    except subprocess.CalledProcessError as e:
        # The tree above already shows the failing command with its stderr.
        print(f"[red]{' '.join(e.cmd)} failed[/red]")
        raise SystemExit(1) from None
    for bundle_name, unsaved in skipped.items():
        print(f"[red]{bundle_name}: not deleted, the work below would be lost (use --force):[/red]")
        for repo, found in unsaved.items():
            print(f"  [yellow]{repo}[/yellow]")
            for kind, lines in found.items():
                print(f"    {kind}")
                for line in lines[:10]:
                    print(f"      {line}")
                if len(lines) > 10:
                    print(f"      ... and {len(lines) - 10} more")
    if skipped:
        raise SystemExit(1)
