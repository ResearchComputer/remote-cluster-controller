"""Slurm job management for ``rcc job``.

This module holds the (testable) business logic; :mod:`rcc.commands.job` is a
thin typer wrapper. The whole point (see issue #1) is that the user never has
to type or shell-quote a Slurm ``--format=`` value, a ``$USER`` expansion, or a
pipeline: rcc composes the remote command and streams the result back.
"""

from __future__ import annotations

import re
import shlex
import sys

from rcc.config import Profile
from rcc.errors import RemoteError
from rcc.ssh import run_remote, run_remote_capture

# Fixed, readable output formats. Plain field names (no width specifiers) so
# they are valid across Slurm versions; squeue/sacct auto-size the columns.
SQUEUE_FORMAT = "JobID,Name,Partition,State,Time,NodeList"
SACCT_FORMAT = "JobID,JobName,Partition,State,Elapsed,ExitCode,Reason,Start,End"

# Slurm's default stdout/stderr file is slurm-%j.out, written in the job's
# submit directory (== remote_dir under rcc). %j -> JOBID.
DEFAULT_LOG_PATTERN = "slurm-{job_id}.out"

_SBATCH_ID_RE = re.compile(r"Submitted batch job (\d+)")


def parse_job_id(sbatch_output: str) -> str | None:
    """Extract the allocated JOBID from sbatch stdout, or None if unrecognized."""
    match = _SBATCH_ID_RE.search(sbatch_output)
    return match.group(1) if match else None


def submit_argv(
    script: str,
    extra_env: list[tuple[str, str]] | None = None,
    *,
    wait: bool = False,
    dependency: str | None = None,
) -> list[str]:
    """Build the sbatch argv for ``job submit``."""
    argv: list[str] = ["sbatch"]
    if extra_env:
        exports = ",".join(f"{key}={value}" for key, value in extra_env)
        argv.append(f"--export=ALL,{exports}")
    if dependency:
        argv.append(f"--dependency={dependency}")
    if wait:
        argv.append("--wait")
    argv.append(script)
    return argv


def cancel_argv(job_id: str) -> list[str]:
    return ["scancel", job_id]


def sacct_argv(job_id: str) -> list[str]:
    return ["sacct", "-j", job_id, f"--format={SACCT_FORMAT}"]


def squeue_script() -> str:
    """Shell snippet for ``job list``.

    Runs under a shell (needs command substitution for the current user) and
    pins a fixed ``--format`` so the user never has to quote one.
    """
    return f'squeue -u "$(id -un)" --format={shlex.quote(SQUEUE_FORMAT)}'


def tail_argv(job_id: str, *, follow: bool, filename: str | None) -> list[str]:
    name = filename or DEFAULT_LOG_PATTERN.format(job_id=job_id)
    if follow:
        return ["tail", "-f", name]
    return ["tail", "-n", "100", name]


def ensure_slurm(profile: Profile) -> None:
    """Raise RemoteError (exit 127) if sbatch is not on the remote PATH."""
    result = run_remote_capture(profile, ["command", "-v", "sbatch"])
    if result.returncode != 0:
        raise RemoteError(
            f"Slurm does not appear to be installed on {profile.host} "
            f"(sbatch not found). Is this an HPC login node?",
            exit_code=127,
        )


def submit(
    profile: Profile,
    script: str,
    extra_env: list[tuple[str, str]] | None = None,
    *,
    wait: bool = False,
    dependency: str | None = None,
) -> int:
    ensure_slurm(profile)
    argv = submit_argv(script, extra_env, wait=wait, dependency=dependency)
    if wait:
        # sbatch --wait blocks until the job completes and exits with the job's
        # exit code. Stream (don't capture) so the user sees the JOBID at once
        # and can interrupt the wait; propagate the exit code to close the
        # submit -> monitor loop.
        return run_remote(profile, argv)
    result = run_remote_capture(profile, argv)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode
    job_id = parse_job_id(result.stdout)
    if job_id is not None:
        print(f"Submitted batch job {job_id}")
    else:
        # Unrecognized sbatch output — surface it verbatim rather than swallow it.
        sys.stdout.write(result.stdout)
    return 0


def list_jobs(profile: Profile) -> int:
    ensure_slurm(profile)
    return run_remote(profile, [], script=squeue_script())


def status(profile: Profile, job_id: str) -> int:
    ensure_slurm(profile)
    return run_remote(profile, sacct_argv(job_id))


def tail(profile: Profile, job_id: str, *, follow: bool, filename: str | None) -> int:
    # No Slurm detection: `tail` is universal, and a missing log file is
    # already reported clearly by tail itself.
    return run_remote(profile, tail_argv(job_id, follow=follow, filename=filename), tty=follow)


def cancel(profile: Profile, job_id: str) -> int:
    ensure_slurm(profile)
    return run_remote(profile, cancel_argv(job_id))
