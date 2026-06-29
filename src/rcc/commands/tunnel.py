from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer

from rcc.config import find_rcc_dir, resolve_profile
from rcc.context import merge_cli_overrides
from rcc.errors import ConfigError
from rcc.ssh import build_ssh_local_forward_args


def tunnel(
    remote_port: int | None = typer.Option(
        None,
        "--remote-port",
        help="Remote port to forward (defaults to profile tunnel.remote_port).",
    ),
    local_port: int | None = typer.Option(
        None,
        "--local-port",
        help="Local port to bind (defaults to profile tunnel.local_port, else remote_port).",
    ),
    remote_host: str | None = typer.Option(
        None,
        "--remote-host",
        help="Remote host the tunnel terminates at (default: localhost; set to a "
        "compute/head node to reach a service not on the login node).",
    ),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Open a local port-forward to the remote, reusing rcc's ControlMaster.

    Replaces the manual ``ssh -L 8080:head:8080 host`` tail every workflow ends
    with (issue #2 P3). Defaults come from the profile's ``[tunnel]`` table, so
    a profile can carry ``tunnel = { remote_port = 8080 }`` and ``rcc tunnel``
    just works. Ctrl-C closes the tunnel.
    """
    if shutil.which("ssh") is None:
        typer.echo("tunneling requires system ssh.", err=True)
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

    rp = remote_port or (resolved.tunnel.remote_port if resolved.tunnel else None)
    if rp is None:
        raise typer.BadParameter(
            "no remote port: pass --remote-port or set [tunnel] remote_port in the profile"
        )
    lp = local_port or (resolved.tunnel.local_port if resolved.tunnel else None) or rp
    rh = remote_host or (resolved.tunnel.remote_host if resolved.tunnel else None) or "localhost"

    argv = build_ssh_local_forward_args(resolved, local_port=lp, remote_port=rp, remote_host=rh)
    typer.echo(
        f"Forwarding local {lp} -> {resolved.host}:{rh}:{rp} "
        "(Ctrl-C to close). Reuses rcc's ControlMaster."
    )

    try:
        subprocess.run(argv)
    except KeyboardInterrupt:
        typer.echo("\ntunnel closed.")
