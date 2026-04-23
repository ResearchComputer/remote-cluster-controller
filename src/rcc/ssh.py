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
    return [
        "-o",
        f"ControlPersist={profile.ssh_control_persist}",
        "-o",
        f"ControlPath={profile.ssh_control_dir}/%C",
        "-o",
        "ControlMaster=auto",
        "-o",
        "LogLevel=ERROR",
    ]


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


def build_rsync_e_string(profile: Profile) -> str:
    return "ssh " + " ".join(shlex.quote(part) for part in _shared_mux_options(profile))


def build_remote_command(remote_dir: str, argv: list[str]) -> str:
    quoted_dir = shlex.quote(remote_dir)
    message = shlex.quote(f"error: remote directory does not exist: {remote_dir}")
    command = " ".join(shlex.quote(arg) for arg in argv) if argv else "exec bash -l"
    return (
        "set -euo pipefail; "
        f"if [[ ! -d {quoted_dir} ]]; then printf '%s\\n' {message} >&2; exit 1; fi; "
        f"cd -- {quoted_dir}; "
        f"{command}"
    )


def _ensure_control_dir(profile: Profile) -> None:
    path = Path(os.path.expanduser(profile.ssh_control_dir))
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError as exc:
        log.warning("failed to chmod %s to 0700: %s", path, exc)


def _primary_available() -> bool:
    return shutil.which("ssh") is not None


def run_remote(profile: Profile, argv: list[str], *, tty: bool = False) -> int:
    """Run argv on remote inside profile.remote_dir. Returns the remote exit code."""
    if _primary_available():
        _ensure_control_dir(profile)
        remote_cmd = build_remote_command(profile.remote_dir, argv)
        full = build_ssh_args(profile, mode="run", tty=tty) + [_bash_lc_arg(remote_cmd)]
        return subprocess.run(full).returncode
    log.warning("ssh not on PATH; using paramiko fallback")
    return _paramiko_run_remote(profile, argv, tty=tty)


def ensure_remote_dir(profile: Profile) -> None:
    """Create profile.remote_dir on the remote host."""
    if _primary_available():
        _ensure_control_dir(profile)
        cmd = f"mkdir -p -- {shlex.quote(profile.remote_dir)}"
        full = build_ssh_args(profile, mode="run", tty=False) + [_bash_lc_arg(cmd)]
        result = subprocess.run(full)
        if result.returncode != 0:
            raise RemoteError(
                f"failed to create remote dir {profile.remote_dir}", exit_code=result.returncode
            )
        return
    log.warning("ssh not on PATH; using paramiko fallback")
    _paramiko_ensure_remote_dir(profile)


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


def _paramiko_run_remote(profile: Profile, argv: list[str], *, tty: bool) -> int:
    client = _paramiko_client(profile)
    try:
        remote_cmd = build_remote_command(profile.remote_dir, argv)
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


def _paramiko_ensure_remote_dir(profile: Profile) -> None:
    client = _paramiko_client(profile)
    try:
        _stdin, stdout, _stderr = client.exec_command(
            f"mkdir -p -- {shlex.quote(profile.remote_dir)}"
        )
        code = stdout.channel.recv_exit_status()
        if code != 0:
            raise RemoteError(f"failed to create remote dir {profile.remote_dir}", exit_code=code)
    finally:
        client.close()
