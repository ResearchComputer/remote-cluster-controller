from __future__ import annotations

from pathlib import Path

import typer

from rcc import slurm
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


def submit(
    script: str = typer.Argument(
        ..., help="sbatch script path on the remote (relative to remote_dir)."
    ),
    extra_env: list[str] | None = typer.Option(
        None,
        "--extra-env",
        metavar="K=V",
        help="Export K=V to the job (repeatable).",
    ),
    wait: bool = typer.Option(
        False,
        "--wait",
        "-W",
        help="Block until the job completes; rcc's exit code becomes the job's exit code.",
    ),
    dependency: str | None = typer.Option(
        None,
        "--dependency",
        metavar="TYPE:JOBID",
        help="Pass --dependency=<...> through to sbatch, e.g. afterok:524614.",
    ),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Submit a Slurm batch script (sbatch) and print the allocated JOBID."""
    env = [_split_env(item) for item in (extra_env or [])]
    resolved = _resolve(profile, host, remote_dir)
    code = slurm.submit(resolved, script, env, wait=wait, dependency=dependency)
    if code != 0:
        raise RemoteError("sbatch exited non-zero", exit_code=code)


def list_jobs(
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """List your Slurm jobs (squeue)."""
    resolved = _resolve(profile, host, remote_dir)
    code = slurm.list_jobs(resolved)
    if code != 0:
        raise RemoteError("squeue exited non-zero", exit_code=code)


def status(
    job_id: str = typer.Argument(..., help="Slurm JOBID."),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Show accounting info for a job (sacct)."""
    resolved = _resolve(profile, host, remote_dir)
    code = slurm.status(resolved, job_id)
    if code != 0:
        raise RemoteError("sacct exited non-zero", exit_code=code)


def tail(
    job_id: str = typer.Argument(..., help="Slurm JOBID."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow the log (tail -f)."),
    filename: str | None = typer.Option(
        None,
        "--file",
        help="Override the log filename (default: slurm-<JOBID>.out in remote_dir).",
    ),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Tail a job's Slurm log file inside remote_dir."""
    resolved = _resolve(profile, host, remote_dir)
    code = slurm.tail(resolved, job_id, follow=follow, filename=filename)
    if code != 0:
        raise RemoteError("tail exited non-zero", exit_code=code)


def cancel(
    job_id: str = typer.Argument(..., help="Slurm JOBID."),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Cancel a Slurm job (scancel)."""
    resolved = _resolve(profile, host, remote_dir)
    code = slurm.cancel(resolved, job_id)
    if code != 0:
        raise RemoteError("scancel exited non-zero", exit_code=code)


def _split_env(item: str) -> tuple[str, str]:
    if "=" not in item:
        raise typer.BadParameter(f"--extra-env expects K=V, got {item!r}")
    key, value = item.split("=", 1)
    if not key:
        raise typer.BadParameter(f"--extra-env key is empty in {item!r}")
    return key, value
