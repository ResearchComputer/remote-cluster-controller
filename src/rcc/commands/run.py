from __future__ import annotations

import json
from pathlib import Path

import typer

from rcc import bg
from rcc.config import find_rcc_dir, resolve_profile
from rcc.context import merge_cli_overrides
from rcc.errors import ConfigError, RemoteError
from rcc.ssh import run_remote, run_remote_tee


def run(
    ctx: typer.Context,
    tty: bool = typer.Option(False, "-t", "--tty", help="Allocate a PTY on the remote"),
    shell: str | None = typer.Option(
        None,
        "-s",
        "--shell",
        help="Interpret the value as a shell snippet, e.g. -s 'squeue -u $USER | head'",
    ),
    env: list[str] | None = typer.Option(
        None,
        "--env",
        metavar="KEY=VAL",
        help="Export KEY=VAL on the remote before the command (repeatable). "
        "Merged on top of the profile's [env] defaults.",
    ),
    env_file: Path | None = typer.Option(
        None,
        "--env-file",
        exists=True,
        readable=True,
        help="Load KEY=VAL lines from a file (like a .env; # comments allowed).",
    ),
    cwd: str | None = typer.Option(
        None,
        "--cwd",
        help="Working directory on the remote (default: profile remote_dir). "
        "Absolute path, or relative to remote_dir.",
    ),
    detach: bool = typer.Option(
        False,
        "--detach",
        help="Launch detached in a tmux session instead of running in the foreground "
        "(non-SLURM hosts). Manage with `rcc bg ps/logs/attach/wait/stop`. "
        "The run survives disconnects.",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help="Name for a detached run (default: auto-generated). Sanitized for tmux.",
    ),
    result_json: Path | None = typer.Option(
        None,
        "--result-json",
        metavar="PATH",
        help="Stream output live AND write {returncode, stdout, stderr, command} to PATH "
        "on exit — so wrappers get both live output and the captured text (tee).",
    ),
    profile: str | None = typer.Option(None, "--profile"),
    host: str | None = typer.Option(None, "--host"),
    remote_dir: str | None = typer.Option(None, "--remote-dir"),
) -> None:
    """Run a command on the remote, inside remote_dir.

    By default the arguments after `--` are passed as separate tokens. Use
    -s/--shell to pass a shell snippet that may contain pipelines, variable
    expansion, or quotes. ``--env KEY=VAL`` / ``--env-file`` inject environment
    on the remote (layered over the profile's ``[env]`` defaults).

    ``--detach`` launches the command in a tmux session so it survives
    disconnects; manage it with ``rcc bg`` (issue #3 #2). ``--result-json PATH``
    tees output to the TTY while writing a structured result, so a wrapper gets
    live output *and* the captured text (issue #2 P1).
    """
    argv = list(ctx.args)
    if shell is None and not argv:
        raise typer.BadParameter(
            "missing remote command (use -- to separate, or -s for a shell snippet)"
        )
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
    merged_env = _resolve_env(resolved.env, env or [], env_file)

    if detach:
        if tty:
            raise typer.BadParameter(
                "--tty is meaningless with --detach (the session has its own pty)"
            )
        session_name = name or _auto_name(shell, argv)
        try:
            final = bg.start(
                resolved, session_name, argv, script=shell, env=merged_env or None, cwd=cwd
            )
        except ValueError as exc:  # bad name
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(
            f"Launched detached run {final!r} on {resolved.host}. "
            f"Logs: {bg.log_path(resolved, final)}. Try: rcc bg logs {final} -f"
        )
        return

    if result_json is not None:
        completed = run_remote_tee(resolved, argv, script=shell, env=merged_env or None, cwd=cwd)
        _write_result_json(result_json, completed, shell=shell, argv=argv)
        if completed.returncode != 0:
            raise RemoteError("remote command exited non-zero", exit_code=completed.returncode)
        return

    code = run_remote(
        resolved,
        argv,
        script=shell,
        tty=tty,
        env=merged_env or None,
        cwd=cwd,
    )
    if code != 0:
        raise RemoteError("remote command exited non-zero", exit_code=code)


def _auto_name(shell: str | None, argv: list[str]) -> str:
    """Generate a readable session name from the command (timestamp-suffixed)."""
    import time

    base = shell or (argv[0] if argv else "run")
    # Keep it tmux-safe and short.
    import re

    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", base.split()[0])[:24].strip("-") or "run"
    return f"{slug}-{int(time.time())}"


def _write_result_json(path: Path, completed, *, shell: str | None, argv: list[str]) -> None:
    payload = {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "command": shell if shell is not None else argv,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _resolve_env(
    profile_env: dict[str, str],
    env_flags: list[str],
    env_file: Path | None,
) -> dict[str, str]:
    merged: dict[str, str] = dict(profile_env)
    if env_file is not None:
        for key, value in _parse_env_file(env_file):
            merged[key] = value
    for item in env_flags:
        key, value = _split_env(item)
        merged[key] = value
    return merged


def _split_env(item: str) -> tuple[str, str]:
    if "=" not in item:
        raise typer.BadParameter(f"--env expects KEY=VAL, got {item!r}")
    key, value = item.split("=", 1)
    if not key:
        raise typer.BadParameter(f"--env key is empty in {item!r}")
    return key, value


def _parse_env_file(path: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise typer.BadParameter(f"{path}:{lineno}: expected KEY=VAL")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(f"{path}:{lineno}: empty key")
        # Strip one layer of matching surrounding quotes, if present.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        pairs.append((key, value))
    return pairs
