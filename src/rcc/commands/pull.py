from __future__ import annotations

from pathlib import Path

import typer

from rcc.config import find_rcc_dir, resolve_profile
from rcc.context import merge_cli_overrides
from rcc.errors import ConfigError
from rcc.sync import pull as _pull


def pull(
    paths: list[str] = typer.Argument(
        None,
        help="Optional remote subpaths to pull (relative to remote_dir). When given, "
        "only those paths are fetched — bypassing rccignore, so ignored result "
        "dirs (e.g. jobs/<sweep>/) can be retrieved.",
    ),
    local_dest: Path | None = typer.Argument(
        None,
        help="Optional local destination root for scoped pulls (default: project root). "
        "The path is reconstructed relative to this root.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n"),
    delete: bool = typer.Option(
        False,
        "--delete",
        help="Sync mode: delete local files (within the non-ignored scope) absent remotely.",
    ),
    mirror: bool = typer.Option(
        False,
        "--mirror",
        help="DANGEROUS full mirror: like --delete but also removes ignored local files. "
        "keep_remote patterns still survive.",
    ),
    no_ignore: bool = typer.Option(
        False, "--no-ignore", help="Bypass .rcc/rccignore for this transfer."
    ),
    exclude: list[str] | None = typer.Option(None, "--exclude"),
    include: list[str] | None = typer.Option(None, "--include"),
    keep_remote: list[str] | None = typer.Option(None, "--keep-remote"),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Pull remote files into the project (or a subpath of it).

    Scoped pull (issue #3 #1): ``rcc pull jobs/sweep/`` fetches just that subtree
    even though ``jobs/`` is in rccignore, removing the hand-rolled ``rsync``
    fallback. The optional second positional is the local destination root.
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
    _pull(
        project_dir=project_dir,
        profile=resolved,
        dry_run=dry_run,
        delete=delete,
        mirror=mirror,
        extra_excludes=exclude or [],
        includes=include or [],
        no_ignore=no_ignore,
        paths=paths,
        local_dest=local_dest,
        keep_remote=keep_remote or [],
    )
