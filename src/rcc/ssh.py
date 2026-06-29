from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from rcc.config import Profile
from rcc.errors import MissingDependencyError, RemoteError

Mode = Literal["run", "mux_check", "mux_exit"]

log = logging.getLogger(__name__)


def _shared_mux_options(profile: Profile) -> list[str]:
    opts: list[str] = [
        "-o",
        f"ControlPersist={profile.ssh_control_persist}",
        "-o",
        f"ControlPath={profile.ssh_control_dir}/%C",
        "-o",
        "ControlMaster=auto",
    ]
    # Bastion hops: encapsulated per-profile instead of a hand-maintained
    # SSH-config dance (issue #2 P3).
    if profile.proxy_jump:
        opts.extend(["-o", f"ProxyJump={profile.proxy_jump}"])
    if profile.identity_file:
        opts.extend(["-o", f"IdentityFile={profile.identity_file}"])
    opts.extend(["-o", "LogLevel=ERROR"])
    return opts


def build_ssh_args(profile: Profile, *, mode: Mode, tty: bool = False) -> list[str]:
    argv: list[str] = ["ssh", *_shared_mux_options(profile)]
    if mode == "run":
        if tty:
            argv.append("-t")
        argv.append(profile.host)
        return argv
    if mode == "mux_check":
        argv.extend(["-O", "check", profile.host])
        return argv
    if mode == "mux_exit":
        argv.extend(["-O", "exit", profile.host])
        return argv
    raise ValueError(f"unknown mode: {mode}")


def build_ssh_local_forward_args(
    profile: Profile,
    *,
    local_port: int,
    remote_port: int,
    remote_host: str = "localhost",
) -> list[str]:
    """Build an ``ssh -N -L`` argv that forwards a local port to the remote.

    Reuses the ControlMaster so the tunnel rides the same connection the rest
    of rcc maintains (issue #2 P3).
    """
    argv: list[str] = ["ssh", *_shared_mux_options(profile)]
    argv.append("-N")
    argv.append("-L")
    argv.append(f"{local_port}:{remote_host}:{remote_port}")
    argv.append(profile.host)
    return argv


def build_rsync_e_string(profile: Profile) -> str:
    return "ssh " + " ".join(shlex.quote(part) for part in _shared_mux_options(profile))


def _export_prefix(env: dict[str, str] | None) -> str:
    if not env:
        return ""
    return "".join(f"export {k}={shlex.quote(v)}; " for k, v in env.items())


def build_remote_command(
    work_dir: str, argv: list[str], *, env: dict[str, str] | None = None
) -> str:
    quoted_dir = shlex.quote(work_dir)
    message = shlex.quote(f"error: remote directory does not exist: {work_dir}")
    command = " ".join(shlex.quote(arg) for arg in argv) if argv else "exec bash -l"
    return (
        "set -euo pipefail; "
        f"{_export_prefix(env)}"
        f"if [[ ! -d {quoted_dir} ]]; then printf '%s\\n' {message} >&2; exit 1; fi; "
        f"cd -- {quoted_dir}; "
        f"{command}"
    )


def build_remote_shell_command(
    work_dir: str, script: str, *, env: dict[str, str] | None = None
) -> str:
    """Like :func:`build_remote_command`, but for a raw shell ``script``.

    The script is inserted verbatim (not per-token quoted) so pipelines, variable
    expansion, redirects, and nested quotes survive the trip to the remote
    shell. This backs ``rcc run -s`` and the Slurm wrappers in :mod:`rcc.slurm`.
    """
    quoted_dir = shlex.quote(work_dir)
    message = shlex.quote(f"error: remote directory does not exist: {work_dir}")
    return (
        "set -euo pipefail; "
        f"{_export_prefix(env)}"
        f"if [[ ! -d {quoted_dir} ]]; then printf '%s\\n' {message} >&2; exit 1; fi; "
        f"cd -- {quoted_dir}; "
        f"{script}"
    )


def _remote_command(
    remote_dir: str,
    argv: list[str],
    script: str | None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> str:
    work_dir = cwd or remote_dir
    if script is not None:
        return build_remote_shell_command(work_dir, script, env=env)
    return build_remote_command(work_dir, argv, env=env)


def _ensure_control_dir(profile: Profile) -> None:
    path = Path(os.path.expanduser(profile.ssh_control_dir))
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError as exc:
        log.warning("failed to chmod %s to 0700: %s", path, exc)


def _primary_available() -> bool:
    return shutil.which("ssh") is not None


def run_remote(
    profile: Profile,
    argv: list[str],
    *,
    script: str | None = None,
    tty: bool = False,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> int:
    """Run argv (or a raw shell ``script``) on remote inside profile.remote_dir.

    Pass ``script`` to interpret a shell snippet verbatim (pipelines, globs,
    variable expansion) — the ``rcc run -s`` path and the basis of the Slurm
    wrappers in :mod:`rcc.slurm`. ``env`` exports extra variables on the remote
    before the command; ``cwd`` overrides the working directory (defaults to
    ``profile.remote_dir``).
    """
    if _primary_available():
        _ensure_control_dir(profile)
        remote_cmd = _remote_command(profile.remote_dir, argv, script, env=env, cwd=cwd)
        full = build_ssh_args(profile, mode="run", tty=tty) + [_bash_lc_arg(remote_cmd)]
        return subprocess.run(full).returncode
    log.warning("ssh not on PATH; using paramiko fallback")
    return _paramiko_run_remote(profile, argv, script=script, tty=tty, env=env, cwd=cwd)


def run_remote_capture(
    profile: Profile,
    argv: list[str],
    *,
    script: str | None = None,
    tty: bool = False,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Like :func:`run_remote`, but capture stdout/stderr instead of streaming."""
    if _primary_available():
        _ensure_control_dir(profile)
        remote_cmd = _remote_command(profile.remote_dir, argv, script, env=env, cwd=cwd)
        full = build_ssh_args(profile, mode="run", tty=tty) + [_bash_lc_arg(remote_cmd)]
        return subprocess.run(full, capture_output=True, text=True)
    return _paramiko_run_remote_capture(profile, argv, script=script, tty=tty, env=env, cwd=cwd)


def run_remote_tee(
    profile: Profile,
    argv: list[str],
    *,
    script: str | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    out=None,
    err=None,
) -> subprocess.CompletedProcess[str]:
    """Stream remote output to ``out``/``err`` live *and* accumulate it.

    This is the 'tee' primitive (issue #2 P1): unlike :func:`run_remote`
    (streams but discards text) and :func:`run_remote_capture` (captures but
    hides output until exit), tee gives callers both — the live bytes go to the
    TTY while a :class:`~subprocess.CompletedProcess` carrying the full text is
    returned. ``rcc run --result-json`` is built on this.
    """
    if out is None:
        out = sys.stdout
    if err is None:
        err = sys.stderr
    if _primary_available():
        _ensure_control_dir(profile)
        remote_cmd = _remote_command(profile.remote_dir, argv, script, env=env, cwd=cwd)
        # No tty: a PTY would merge stderr into stdout and defeat separate teeing.
        full = build_ssh_args(profile, mode="run", tty=False) + [_bash_lc_arg(remote_cmd)]
        return _popen_tee(full, out, err, argv)
    log.warning("ssh not on PATH; using paramiko fallback")
    return _paramiko_run_remote_tee(
        profile, argv, script=script, env=env, cwd=cwd, out=out, err=err
    )


def _popen_tee(full_argv: list[str], out, err, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Popen ssh, pump stdout/stderr to ``out``/``err`` while accumulating."""
    import threading

    proc = subprocess.Popen(full_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out_buf = bytearray()
    err_buf = bytearray()

    def pump(src, dst, buf: bytearray) -> None:
        assert src is not None
        while True:
            chunk = src.read(4096)
            if not chunk:
                break
            buf.extend(chunk)
            _write_chunk(dst, chunk)

    readers = [
        threading.Thread(target=pump, args=(proc.stdout, out, out_buf)),
        threading.Thread(target=pump, args=(proc.stderr, err, err_buf)),
    ]
    for reader in readers:
        reader.start()
    code = proc.wait()
    for reader in readers:
        reader.join()
    return subprocess.CompletedProcess(
        args=argv,
        returncode=code,
        stdout=out_buf.decode("utf-8", "replace"),
        stderr=err_buf.decode("utf-8", "replace"),
    )


def _write_chunk(stream, chunk: bytes) -> None:
    # Prefer the binary buffer (text streams) so bytes pass through untouched;
    # fall back to text decode for objects without .buffer (StringIO in tests).
    buf = getattr(stream, "buffer", None)
    if buf is not None:
        buf.write(chunk)
        buf.flush()
    else:
        stream.write(chunk.decode("utf-8", "replace"))
        stream.flush()


def ensure_remote_dir(profile: Profile) -> None:
    """Create profile.remote_dir on the remote host."""
    ensure_remote_path(profile, profile.remote_dir)


def ensure_remote_path(profile: Profile, remote_path: str) -> None:
    """Create an arbitrary remote directory path (for scoped syncs)."""
    if _primary_available():
        _ensure_control_dir(profile)
        cmd = f"mkdir -p -- {shlex.quote(remote_path)}"
        full = build_ssh_args(profile, mode="run", tty=False) + [_bash_lc_arg(cmd)]
        result = subprocess.run(full)
        if result.returncode != 0:
            raise RemoteError(
                f"failed to create remote dir {remote_path}", exit_code=result.returncode
            )
        return
    log.warning("ssh not on PATH; using paramiko fallback")
    _paramiko_ensure_remote_dir(profile, path=remote_path)


def mux_check(profile: Profile) -> bool:
    if not _primary_available():
        return False
    _ensure_control_dir(profile)
    result = subprocess.run(
        build_ssh_args(profile, mode="mux_check"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def mux_exit(profile: Profile) -> None:
    if not _primary_available():
        raise MissingDependencyError("ControlMaster not available without system ssh")
    _ensure_control_dir(profile)
    result = subprocess.run(
        build_ssh_args(profile, mode="mux_exit"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RemoteError("failed to close ControlMaster", exit_code=result.returncode)


def _paramiko_client(profile: Profile):
    try:
        import paramiko
    except ImportError as exc:
        raise MissingDependencyError("paramiko required for fallback; install paramiko") from exc
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(hostname=profile.host)
    return client


def _bash_lc_arg(command: str) -> str:
    return f"bash -lc {shlex.quote(command)}"


def _paramiko_run_remote(
    profile: Profile,
    argv: list[str],
    *,
    script: str | None,
    tty: bool,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> int:
    client = _paramiko_client(profile)
    try:
        remote_cmd = _remote_command(profile.remote_dir, argv, script, env=env, cwd=cwd)
        transport = client.get_transport()
        if transport is None:
            raise RemoteError("paramiko: could not open transport")
        channel = transport.open_session()
        if tty:
            channel.get_pty()
        channel.exec_command(f"bash -lc {shlex.quote(remote_cmd)}")
        while True:
            if channel.recv_ready():
                data = channel.recv(4096)
                if data:
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
            if channel.recv_stderr_ready():
                err = channel.recv_stderr(4096)
                if err:
                    sys.stderr.buffer.write(err)
                    sys.stderr.buffer.flush()
            if (
                channel.exit_status_ready()
                and not channel.recv_ready()
                and not channel.recv_stderr_ready()
            ):
                break
        return channel.recv_exit_status()
    finally:
        client.close()


def _paramiko_run_remote_capture(
    profile: Profile,
    argv: list[str],
    *,
    script: str | None,
    tty: bool,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    client = _paramiko_client(profile)
    try:
        remote_cmd = _remote_command(profile.remote_dir, argv, script, env=env, cwd=cwd)
        transport = client.get_transport()
        if transport is None:
            raise RemoteError("paramiko: could not open transport")
        channel = transport.open_session()
        if tty:
            channel.get_pty()
        channel.exec_command(f"bash -lc {shlex.quote(remote_cmd)}")
        out = bytearray()
        err = bytearray()
        while True:
            if channel.recv_ready():
                data = channel.recv(4096)
                if data:
                    out.extend(data)
            if channel.recv_stderr_ready():
                data = channel.recv_stderr(4096)
                if data:
                    err.extend(data)
            if (
                channel.exit_status_ready()
                and not channel.recv_ready()
                and not channel.recv_stderr_ready()
            ):
                break
        code = channel.recv_exit_status()
        return subprocess.CompletedProcess(
            args=argv,
            returncode=code,
            stdout=out.decode("utf-8", "replace"),
            stderr=err.decode("utf-8", "replace"),
        )
    finally:
        client.close()


def _paramiko_run_remote_tee(
    profile: Profile,
    argv: list[str],
    *,
    script: str | None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    out=None,
    err=None,
) -> subprocess.CompletedProcess[str]:
    """Paramiko-backed tee: write live bytes to out/err while accumulating."""
    client = _paramiko_client(profile)
    try:
        remote_cmd = _remote_command(profile.remote_dir, argv, script, env=env, cwd=cwd)
        transport = client.get_transport()
        if transport is None:
            raise RemoteError("paramiko: could not open transport")
        channel = transport.open_session()
        channel.exec_command(f"bash -lc {shlex.quote(remote_cmd)}")
        out_buf = bytearray()
        err_buf = bytearray()
        while True:
            if channel.recv_ready():
                data = channel.recv(4096)
                if data:
                    out_buf.extend(data)
                    _write_chunk(out, data)
            if channel.recv_stderr_ready():
                data = channel.recv_stderr(4096)
                if data:
                    err_buf.extend(data)
                    _write_chunk(err, data)
            if (
                channel.exit_status_ready()
                and not channel.recv_ready()
                and not channel.recv_stderr_ready()
            ):
                break
        code = channel.recv_exit_status()
        return subprocess.CompletedProcess(
            args=argv,
            returncode=code,
            stdout=out_buf.decode("utf-8", "replace"),
            stderr=err_buf.decode("utf-8", "replace"),
        )
    finally:
        client.close()


def _paramiko_ensure_remote_dir(profile: Profile, *, path: str | None = None) -> None:
    client = _paramiko_client(profile)
    try:
        target = path if path is not None else profile.remote_dir
        _stdin, stdout, _stderr = client.exec_command(f"mkdir -p -- {shlex.quote(target)}")
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RemoteError(f"failed to create remote dir {target}", exit_code=code)
    finally:
        client.close()
