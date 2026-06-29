from __future__ import annotations

from pathlib import Path

import typer

from rcc import bg
from rcc.config import Profile, find_rcc_dir, resolve_profile
from rcc.context import merge_cli_overrides
from rcc.errors import ConfigError, RemoteError


def _resolve(profile: str | None, host: str | None, remote_dir: str | None) -> Profile:
    rcc_dir = find_rcc_dir(Path.cwd())
    if rcc_dir is None:
        raise ConfigError("no .rcc/ found (run 'rcc init')")
    overrides = merge_cli_overrides(profile=profile, host=host, remote_dir=remote_dir)
    return resolve_profile(
        rcc_dir.parent,
        profile_name=overrides.profile,
        host_override=overrides.host,
        remote_dir_override=overrides.remote_dir,
    )


def ps(
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """List running rcc detached sessions (tmux)."""
    resolved = _resolve(profile, host, remote_dir)
    sessions = bg.list_sessions(resolved)
    if not sessions:
        typer.echo("No running rcc detached sessions.")
        return
    typer.echo(f"{'NAME':<24} {'CREATED':<20} ATTACHED")
    for s in sessions:
        typer.echo(f"{s.name:<24} {s.created:<20} {s.attached}")


def logs(
    name: str | None = typer.Argument(None, help="Session name (default: the sole running one)."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow the log (tail -f)."),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Tail the log of a detached run (in remote_dir/.rcc-runs/<name>.log)."""
    resolved = _resolve(profile, host, remote_dir)
    target = bg.resolve_name(resolved, name)
    code = bg.logs(resolved, target, follow=follow)
    if code != 0:
        raise RemoteError("tail exited non-zero", exit_code=code)


def attach(
    name: str | None = typer.Argument(None, help="Session name (default: the sole running one)."),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Attach to a running detached session (tmux attach)."""
    resolved = _resolve(profile, host, remote_dir)
    target = bg.resolve_name(resolved, name)
    code = bg.attach(resolved, target)
    if code != 0:
        raise RemoteError("tmux attach exited non-zero", exit_code=code)


def wait(
    name: str | None = typer.Argument(None, help="Session name (default: the sole running one)."),
    timeout: float | None = typer.Option(
        None, "--timeout", metavar="SECONDS", help="Give up after SECONDS (exit 124)."
    ),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Block until a detached run finishes; exit with its exit code.

    Non-zero on a failed/killed run; 124 on timeout. This closes the
    launch→monitor loop for non-SLURM hosts in one command (issue #3 #2).
    """
    resolved = _resolve(profile, host, remote_dir)
    target = bg.resolve_name(resolved, name)
    code = bg.wait(resolved, target, timeout=timeout)
    if code is None:
        typer.echo(f"timed out waiting for {target!r}", err=True)
        raise typer.Exit(code=124)
    if code != 0:
        raise RemoteError(f"detached run {target!r} exited with code {code}", exit_code=code)


def stop(
    name: str | None = typer.Argument(None, help="Session name (default: the sole running one)."),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Kill a detached run (tmux kill-session)."""
    resolved = _resolve(profile, host, remote_dir)
    target = bg.resolve_name(resolved, name)
    code = bg.stop(resolved, target)
    if code != 0:
        raise RemoteError("tmux kill-session exited non-zero", exit_code=code)
    typer.echo(f"Stopped {target}.")
