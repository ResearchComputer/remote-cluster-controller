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
import time
from dataclasses import dataclass

from rcc.config import Profile
from rcc.errors import RemoteError
from rcc.ssh import run_remote, run_remote_capture

# Fixed, readable output formats. Plain field names (no width specifiers) so
# they are valid across Slurm versions; squeue/sacct auto-size the columns.
SQUEUE_FORMAT = "JobID,Name,Partition,State,Time,NodeList"
SACCT_FORMAT = "JobID,JobName,Partition,State,Elapsed,ExitCode,Reason,Start,End"

# Pipe-delimited formats for ``--json``. For squeue we emit explicit ``%`` codes
# joined by ``|`` under ``-h`` (no header): we control both the delimiter and
# the field order, so parsing never depends on ``--parsable`` being available.
# The field-name list below MUST stay paired with the %-code order.
SQUEUE_JSON_FORMAT = "%i|%j|%P|%T|%M|%N"
SQUEUE_JSON_FIELDS = ["JobID", "Name", "Partition", "State", "Time", "NodeList"]

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


# --------------------------------------------------------------------------- #
# job wait: poll squeue, then classify the final state via sacct (issue #2 P2)
# --------------------------------------------------------------------------- #

# State machines are sticky; treat anything *not* explicitly successful as a
# failure for exit-code purposes. Slurm states are uppercase; we match prefixes.
_OK_STATES = ("COMPLETED",)


@dataclass(frozen=True)
class JobOutcome:
    state: str | None
    exit_code: int | None  # raw numeric portion of sacct ExitCode (before ':'N)
    raw: str

    @property
    def ok(self) -> bool:
        return self.state in _OK_STATES


def squeue_state_argv(job_id: str) -> list[str]:
    """One state per line for a job (-h suppresses the header). Empty if the job
    has left the queue (finished/cancelled)."""
    return ["squeue", "-j", job_id, "-h", "-o", "%T"]


def sacct_outcome_argv(job_id: str) -> list[str]:
    """Parsable, no-header, |-separated State|ExitCode for a finished job."""
    return ["sacct", "-j", job_id, "-n", "-P", "--format=State,ExitCode"]


def parse_outcome(output: str) -> JobOutcome:
    """Parse the first data line of sacct into a JobOutcome (defensive)."""
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        state = parts[0].strip() if parts else ""
        exit_code = None
        if len(parts) > 1:
            num = parts[1].strip().split(":", 1)[0]
            try:
                exit_code = int(num)
            except ValueError:
                exit_code = None
        return JobOutcome(state=state or None, exit_code=exit_code, raw=line)
    return JobOutcome(state=None, exit_code=None, raw="")


def outcome_to_exit_code(outcome: JobOutcome) -> int:
    """Map a finished job's outcome to an rcc exit code.

    A COMPLETED job is 0; otherwise prefer the recorded numeric exit code, then a
    non-zero sentinel (1) so a wrapper's submit->wait loop sees the failure.
    """
    if outcome.ok:
        return 0
    if outcome.exit_code is not None:
        return outcome.exit_code
    return 1


def wait(
    profile: Profile,
    job_id: str,
    *,
    on: str | None = None,
    timeout: float | None = None,
    poll: float = 5.0,
) -> int:
    """Block until ``job_id`` leaves the queue (or reaches state ``on``).

    Returns an rcc exit code: 0 if the job COMPLETED, the job's recorded exit
    code (or 1) otherwise. Exits the wait early if ``on`` is reached. Returns
    124 on timeout (mirroring the ``timeout(1)`` convention).
    """
    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        result = run_remote_capture(profile, squeue_state_argv(job_id))
        states = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        in_queue = bool(states)
        if on is not None and any(s == on for s in states):
            return 0
        if not in_queue:
            break
        if deadline is not None and time.monotonic() >= deadline:
            return 124
        time.sleep(poll)
    # Job has left the queue: classify the final state via sacct.
    sacct = run_remote_capture(profile, sacct_outcome_argv(job_id))
    outcome = parse_outcome(sacct.stdout)
    return outcome_to_exit_code(outcome)


# --------------------------------------------------------------------------- #
# Structured (JSON) job listing/status (issue #2 P2)
# --------------------------------------------------------------------------- #


def squeue_json_script() -> str:
    """squeue as pipe-delimited rows with a known, rcc-defined field order.

    ``-h`` drops the header; the ``|``s inside ``--format`` are our delimiter,
    and :data:`SQUEUE_JSON_FIELDS` is the paired column list. We don't rely on
    ``--parsable`` (its availability/meaning varies across squeue versions).
    """
    return f'squeue -h -u "$(id -un)" --format={shlex.quote(SQUEUE_JSON_FORMAT)}'


def sacct_json_argv(job_id: str) -> list[str]:
    """sacct with ``--parsable`` (the well-established ``-P``) keeping its header.

    The header line carries the actual field names, so parsing survives sacct
    renaming or reordering fields across versions.
    """
    return ["sacct", "-j", job_id, "--parsable", f"--format={SACCT_FORMAT}"]


def parse_rows(output: str, fields: list[str] | None = None) -> list[dict[str, str]]:
    """Parse pipe-delimited Slurm output into a list of ``{field: value}`` dicts.

    Pure / testable. If ``fields`` is given, every non-empty line is a data row
    in that fixed column order (use with ``-h``/``--noheader`` output, e.g.
    squeue). If ``fields`` is None, the first line is a header carrying field
    names (use with ``--parsable`` header output, e.g. sacct). Ragged rows are
    padded with empty strings so a dropped field never misaligns later columns.
    """
    lines = [ln for ln in output.splitlines() if ln.strip()]
    if not lines:
        return []
    if fields is None:
        header = [c.strip() for c in lines[0].split("|")]
        lines = lines[1:]
        if not lines:
            return []
    else:
        header = fields
    rows: list[dict[str, str]] = []
    for line in lines:
        values = [c.strip() for c in line.split("|")]
        if len(values) < len(header):
            values += [""] * (len(header) - len(values))
        rows.append(dict(zip(header, values)))
    return rows


def _parse_exit_code_field(value: str) -> tuple[int | None, int | None]:
    """Split a Slurm ``ExitCode`` of the form ``RETURN:SIGNAL`` into ints."""
    try:
        ret_part, sig_part = value.split(":", 1)
        return int(ret_part), int(sig_part)
    except (ValueError, IndexError):
        return None, None


def _enrich(row: dict[str, str]) -> dict[str, object]:
    """Augment a raw parsed row with derived, wrapper-friendly fields.

    Adds ``exit_code``/``signal`` (parsed from ``ExitCode``) and ``ok`` (bool,
    from ``State``) when the source fields are present. Leaves all original
    fields intact so consumers keep the raw text too.
    """
    out: dict[str, object] = dict(row)
    ec = row.get("ExitCode")
    if ec:
        rc, sig = _parse_exit_code_field(ec)
        out["exit_code"] = rc
        out["signal"] = sig
    state = row.get("State")
    if state:
        out["ok"] = state in _OK_STATES
    return out


def list_jobs_json(profile: Profile) -> list[dict[str, object]]:
    """Return the current user's queued/running jobs as structured records."""
    ensure_slurm(profile)
    result = run_remote_capture(profile, [], script=squeue_json_script())
    return [_enrich(r) for r in parse_rows(result.stdout, SQUEUE_JSON_FIELDS)]


def status_json(profile: Profile, job_id: str) -> list[dict[str, object]]:
    """Return sacct records for ``job_id`` as structured records.

    sacct emits one row for the main job and one per step (``<id>.batch``,
    ``<id>.extern``, ``<id>.0`` ...); all are returned — the ``JobID`` field
    distinguishes them. Each row with a ``State`` gets an ``ok`` flag and each
    row with an ``ExitCode`` gets parsed ``exit_code``/``signal`` ints.
    """
    ensure_slurm(profile)
    result = run_remote_capture(profile, sacct_json_argv(job_id))
    return [_enrich(r) for r in parse_rows(result.stdout)]
