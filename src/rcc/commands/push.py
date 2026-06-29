from __future__ import annotations

from pathlib import Path

import typer

from rcc.config import find_rcc_dir, resolve_profile
from rcc.context import merge_cli_overrides
from rcc.errors import ConfigError
from rcc.sync import push as _push


def push(
    paths: list[str] = typer.Argument(
        None,
        help="Optional subpaths to push (relative to the project root). When given, "
        "only those paths are transferred and rccignore is bypassed for them.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n"),
    delete: bool = typer.Option(
        False,
        "--delete",
        help="Sync mode: delete remote files (within the non-ignored transfer scope) "
        "that no longer exist locally.",
    ),
    mirror: bool = typer.Option(
        False,
        "--mirror",
        help="DANGEROUS full mirror: like --delete but also removes rccignore-excluded "
        "remote files (--delete-excluded). keep_remote patterns still survive.",
    ),
    no_ignore: bool = typer.Option(
        False, "--no-ignore", help="Bypass .rcc/rccignore for this transfer."
    ),
    exclude: list[str] | None = typer.Option(None, "--exclude"),
    include: list[str] | None = typer.Option(
        None, "--include", help="Extra include glob (repeatable)."
    ),
    keep_remote: list[str] | None = typer.Option(
        None,
        "--keep-remote",
        help="Remote glob to protect from --delete/--mirror even if ignored "
        "(repeatable). Stacks on top of the profile's keep_remote.",
    ),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Push project (or a subpath) to remote.

    Deletion safety (issue #2 P1): the default is non-destructive. ``--delete``
    is a bounded sync (excluded files are spared); ``--mirror`` is the explicit
    dangerous full mirror; ``--keep-remote`` / profile ``keep_remote`` protect
    job-owned paths (logs/, cache/, *.safetensors, ...) from either.
    """
    rcc_dir = find_rcc_dir(Path.cwd())
    if rcc_dir is None:
        raise ConfigError("no .rcc/ found (run 'rcc init')")
    project_dir = rcc_dir.parent
    overrides = merge_cli_overrides(profile=profile, host=host, remote_dir=remote_dir)
    resolved = resolve_profile(
        project_dir,
        profile_name=overrides.profile,
        host_override=overrides.host,
        remote_dir_override=overrides.remote_dir,
    )
    if delete and mirror:
        raise typer.BadParameter("use either --delete or --mirror, not both")
    _push(
        project_dir=project_dir,
        profile=resolved,
        dry_run=dry_run,
        delete=delete,
        mirror=mirror,
        extra_excludes=exclude or [],
        includes=include or [],
        no_ignore=no_ignore,
        paths=paths,
        keep_remote=keep_remote or [],
    )
