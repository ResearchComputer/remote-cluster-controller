from __future__ import annotations

import shutil
from pathlib import Path

import typer

from rcc.config import find_rcc_dir, resolve_profile
from rcc.context import merge_cli_overrides
from rcc.errors import ConfigError
from rcc.ssh import mux_check, mux_exit


def close(
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Close the SSH ControlMaster connection for the profile's host."""
    if shutil.which("ssh") is None:
        typer.echo("ControlMaster not available without system ssh.", err=True)
        raise typer.Exit(code=1)
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
    if mux_check(resolved):
        mux_exit(resolved)
        typer.echo(f"Closed ControlMaster for {resolved.host}.")
    else:
        typer.echo(f"ControlMaster was not open for {resolved.host}.")
