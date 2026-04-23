from __future__ import annotations

from pathlib import Path

import typer

from rcc.config import find_rcc_dir, resolve_profile
from rcc.context import merge_cli_overrides
from rcc.errors import ConfigError
from rcc.sync import push as _push


def push(
    dry_run: bool = typer.Option(False, "--dry-run", "-n"),
    delete: bool = typer.Option(False, "--delete"),
    exclude: list[str] | None = typer.Option(None, "--exclude"),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Push project to remote."""
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
    _push(
        project_dir=project_dir,
        profile=resolved,
        dry_run=dry_run,
        delete=delete,
        extra_excludes=exclude or [],
    )
