from __future__ import annotations

from pathlib import Path

import typer

from rcc.config import find_rcc_dir, resolve_profile
from rcc.context import merge_cli_overrides
from rcc.errors import ConfigError


def config(
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Print the resolved profile after CLI overrides."""
    rcc_dir = find_rcc_dir(Path.cwd())
    if rcc_dir is None:
        raise ConfigError("no .rcc/ found (run 'rcc init')")
    overrides = merge_cli_overrides(profile=profile, host=host, remote_dir=remote_dir)
    resolved = resolve_profile(
        rcc_dir.parent,
        profile_name=overrides.profile,
        host_override=overrides.host,
        remote_dir_override=overrides.remote_dir,
    )
    typer.echo(f"host = {resolved.host!r}")
    typer.echo(f"remote_dir = {resolved.remote_dir!r}")
    typer.echo(f"ssh_control_persist = {resolved.ssh_control_persist!r}")
    typer.echo(f"ssh_control_dir = {resolved.ssh_control_dir!r}")
