"""Detached/background runs for non-SLURM hosts, backed by ``tmux``.

This module mirrors :mod:`rcc.slurm`'s shape (testable business logic; a thin
typer wrapper in :mod:`rcc.commands.bg`): it gives non-SLURM SSH hosts the same
"kick off a long job, watch it, fetch results" UX that ``rcc job`` gives Slurm,
removing the tmux/nohup orchestration every consumer used to hand-roll
(issue #3 #2 — the single biggest ask there).

State on the remote (all under ``remote_dir/.rcc-runs/``, so it survives
disconnects and gets pulled by ``rcc pull`` once added to an include)::

    <remote_dir>/.rcc-runs/<name>.log      # tee'd stdout+stderr of the command
    <remote_dir>/.rcc-runs/<name>.status   # exit code, written when it finishes

A session runs in a tmux session named ``rcc-<name>`` so ``attach`` can reach it
and so ``ps``/``stop`` can enumerate just the rcc-launched ones.
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass

from rcc.config import Profile
from rcc.errors import RemoteError
from rcc.ssh import run_remote, run_remote_capture

SESSION_PREFIX = "rcc-"
RUN_DIR = ".rcc-runs"  # relative to remote_dir

# tmux forbids '.' and ':' in session names; sanitize so a caller-supplied name
# (e.g. "sweep.3") doesn't break new-session.
_BAD_SESSION_CHARS = re.compile(r"[.:]")


def sanitize(name: str) -> str:
    cleaned = _BAD_SESSION_CHARS.sub("-", name.strip().strip("/"))
    if not cleaned:
        raise ValueError(f"empty bg name after sanitizing {name!r}")
    return cleaned


def session(name: str) -> str:
    return SESSION_PREFIX + sanitize(name)


def log_path(profile: Profile, name: str) -> str:
    return f"{profile.remote_dir.rstrip('/')}/{RUN_DIR}/{sanitize(name)}.log"


def status_path(profile: Profile, name: str) -> str:
    return f"{profile.remote_dir.rstrip('/')}/{RUN_DIR}/{sanitize(name)}.status"


def resolve_cwd(remote_dir: str, cwd: str | None) -> str:
    """Resolve a working directory: absolute wins; relative is joined to remote_dir."""
    if not cwd:
        return remote_dir
    if cwd.startswith("/"):
        return cwd
    return f"{remote_dir.rstrip('/')}/{cwd}"


def _export(env: dict[str, str] | None) -> str:
    if not env:
        return ""
    return "".join(f"export {k}={shlex.quote(v)}; " for k, v in env.items())


def start_argv(
    profile: Profile,
    name: str,
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> list[str]:
    """Build the remote argv that launches a detached tmux session for ``argv``."""
    return _start_argv(profile, name, _argv_to_shell(argv), env=env, cwd=cwd)


def start_script_argv(
    profile: Profile,
    name: str,
    script: str,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> list[str]:
    """Like :func:`start_argv`, but for a raw shell ``script`` (verbatim)."""
    return _start_argv(profile, name, script, env=env, cwd=cwd)


def _start_argv(profile: Profile, name: str, cmd_str: str, *, env, cwd) -> list[str]:
    s = sanitize(name)
    base = f"{profile.remote_dir.rstrip('/')}/{RUN_DIR}"
    logp = shlex.quote(f"{base}/{s}.log")
    statp = shlex.quote(f"{base}/{s}.status")
    logdir = shlex.quote(base)
    work = shlex.quote(resolve_cwd(profile.remote_dir, cwd))
    exports = _export(env)
    # The wrapper: mkdir the run dir, export env, cd, run the command under
    # pipefail inside a subshell whose stdout+stderr are teed to the log file,
    # then unconditionally record the command's exit code (PIPESTATUS[0]) to
    # the status file. tmux runs this detached, so rcc returns at once.
    inner = (
        "set -o pipefail; "
        f"mkdir -p -- {logdir}; "
        f"{exports}"
        f"cd -- {work}; "
        f"( set -euo pipefail; {cmd_str} ) 2>&1 | tee -- {logp}; "
        f"printf '%s' \"${{PIPESTATUS[0]}}\" > {statp}"
    )
    return ["tmux", "new-session", "-d", "-s", SESSION_PREFIX + s, "bash", "-lc", inner]


def _argv_to_shell(argv: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in argv)


def list_sessions_script() -> str:
    """Shell snippet enumerating tmux sessions (name\\tcreated\\tattached).

    Suppressed stderr + ``|| true`` so a host with no sessions (or no server)
    yields empty output instead of an error.
    """
    fmt = "#{session_name}\\t#{session_created_string}\\t#{session_attached}"
    return f"tmux list-sessions -F {shlex.quote(fmt)} 2>/dev/null || true"


@dataclass(frozen=True)
class SessionInfo:
    name: str  # sanitized, without the rcc- prefix
    created: str
    attached: str


def parse_sessions(output: str) -> list[SessionInfo]:
    infos: list[SessionInfo] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or not line.startswith(SESSION_PREFIX):
            continue
        parts = line.split("\t")
        raw_name = parts[0][len(SESSION_PREFIX) :]
        created = parts[1] if len(parts) > 1 else ""
        attached = parts[2] if len(parts) > 2 else ""
        infos.append(SessionInfo(name=raw_name, created=created, attached=attached))
    return infos


def has_session_argv(name: str) -> list[str]:
    return ["tmux", "has-session", "-t", session(name)]


def kill_session_argv(name: str) -> list[str]:
    return ["tmux", "kill-session", "-t", session(name)]


def log_tail_argv(profile: Profile, name: str, *, follow: bool) -> list[str]:
    if follow:
        return ["tail", "-f", "--", log_path(profile, name)]
    return ["tail", "-n", "200", "--", log_path(profile, name)]


def attach_argv(name: str) -> list[str]:
    return ["tmux", "attach", "-t", session(name)]


def ensure_tmux(profile: Profile) -> None:
    """Raise RemoteError (exit 127) if tmux is not on the remote PATH."""
    result = run_remote_capture(profile, ["command", "-v", "tmux"])
    if result.returncode != 0:
        raise RemoteError(
            f"tmux does not appear to be installed on {profile.host} "
            "(needed for detached runs). Install tmux on the remote.",
            exit_code=127,
        )


def start(
    profile: Profile,
    name: str,
    argv: list[str],
    *,
    script: str | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> str:
    """Launch a detached run; return the (sanitized) session name."""
    ensure_tmux(profile)
    if script is not None:
        launch_argv = start_script_argv(profile, name, script, env=env, cwd=cwd)
    else:
        launch_argv = start_argv(profile, name, argv, env=env, cwd=cwd)
    result = run_remote_capture(profile, launch_argv)
    if result.returncode != 0:
        # tmux new-session returns non-zero if the session name is taken or tmux
        # is broken; surface stderr verbatim.
        raise RemoteError(
            f"failed to start detached run {name!r}: {result.stderr.strip() or 'tmux error'}",
            exit_code=result.returncode,
        )
    return sanitize(name)


def list_sessions(profile: Profile) -> list[SessionInfo]:
    result = run_remote_capture(profile, [], script=list_sessions_script())
    return parse_sessions(result.stdout)


def has_session(profile: Profile, name: str) -> bool:
    return run_remote_capture(profile, has_session_argv(name)).returncode == 0


def read_status(profile: Profile, name: str) -> int | None:
    """Read the recorded exit code for a finished run, or None if unavailable."""
    result = run_remote_capture(profile, ["cat", "--", status_path(profile, name)])
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def wait(
    profile: Profile, name: str, *, timeout: float | None = None, poll: float = 1.0
) -> int | None:
    """Block until the detached run finishes. Returns its exit code, or None on timeout.

    Polls ``tmux has-session``; once the session is gone, reads ``<name>.status``
    for the exit code the wrapper recorded. Returns None if ``timeout`` elapses.
    """
    deadline = time.monotonic() + timeout if timeout is not None else None
    while has_session(profile, name):
        if deadline is not None and time.monotonic() >= deadline:
            return None
        time.sleep(poll)
    return read_status(profile, name)


def stop(profile: Profile, name: str) -> int:
    return run_remote(profile, kill_session_argv(name))


def logs(profile: Profile, name: str, *, follow: bool) -> int:
    return run_remote(profile, log_tail_argv(profile, name, follow=follow), tty=follow)


def attach(profile: Profile, name: str) -> int:
    return run_remote(profile, attach_argv(name), tty=True)


def resolve_name(profile: Profile, name: str | None) -> str:
    """Default ``name`` to the sole running session, else error helpfully."""
    if name is not None:
        return name
    sessions = list_sessions(profile)
    if len(sessions) == 1:
        return sessions[0].name
    if not sessions:
        raise RemoteError("no running rcc detached sessions; pass a name")
    raise RemoteError(
        "multiple sessions running; pass a name: " + ", ".join(s.name for s in sessions)
    )
