from __future__ import annotations

from pathlib import Path

import typer

from rcc.config import find_rcc_dir, resolve_profile
from rcc.context import merge_cli_overrides
from rcc.errors import ConfigError, RemoteError
from rcc.ssh import run_remote


def run(
    ctx: typer.Context,
    tty: bool = typer.Option(False, "-t", "--tty", help="Allocate a PTY on the remote"),
    shell: str | None = typer.Option(
        None,
        "-s",
        "--shell",
        help="Interpret the value as a shell snippet, e.g. -s 'squeue -u $USER | head'",
    ),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Run a command on the remote, inside remote_dir.

    By default the arguments after `--` are passed as separate tokens. Use
    -s/--shell to pass a shell snippet that may contain pipelines, variable
    expansion, or quotes.
    """
    argv = list(ctx.args)
    if shell is None and not argv:
        raise typer.BadParameter("missing remote command (use -- to separate, or -s for a shell snippet)")
    if shell is not None and argv:
        raise typer.BadParameter("-s/--shell cannot be combined with extra positional arguments")
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
    code = run_remote(resolved, argv, script=shell, tty=tty)
    if code != 0:
        raise RemoteError("remote command exited non-zero", exit_code=code)
