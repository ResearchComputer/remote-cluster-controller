from __future__ import annotations

import logging
import shutil
from pathlib import Path

from rcc._paramiko_fallback import build_matcher, plan_pull_transfers, plan_push_transfers
from rcc._rsync import build_rsync_argv, run_rsync
from rcc.config import Profile
from rcc.errors import MissingDependencyError
from rcc.ssh import build_rsync_e_string, ensure_remote_dir

log = logging.getLogger(__name__)


def _rccignore_path(project_dir: Path) -> Path:
    return project_dir / ".rcc" / "rccignore"


def _primary_available() -> bool:
    return shutil.which("ssh") is not None and shutil.which("rsync") is not None


def push(
    *,
    project_dir: Path,
    profile: Profile,
    dry_run: bool,
    delete: bool,
    extra_excludes: list[str],
) -> None:
    if _primary_available():
        ensure_remote_dir(profile)
        argv = build_rsync_argv(
            source=project_dir,
            destination=f"{profile.host}:{profile.remote_dir}",
            e_string=build_rsync_e_string(profile),
            exclude_from=_rccignore_path(project_dir),
            extra_excludes=extra_excludes,
            dry_run=dry_run,
            delete=delete,
        )
        run_rsync(argv)
        return

    log.warning("rsync/ssh not on PATH; using paramiko fallback")
    _paramiko_push(
        project_dir, profile, dry_run=dry_run, delete=delete, extra_excludes=extra_excludes
    )


def pull(
    *,
    project_dir: Path,
    profile: Profile,
    dry_run: bool,
    delete: bool,
    extra_excludes: list[str],
) -> None:
    if _primary_available():
        argv = build_rsync_argv(
            source=f"{profile.host}:{profile.remote_dir}",
            destination=project_dir,
            e_string=build_rsync_e_string(profile),
            exclude_from=_rccignore_path(project_dir),
            extra_excludes=extra_excludes,
            dry_run=dry_run,
            delete=delete,
        )
        run_rsync(argv)
        return

    log.warning("rsync/ssh not on PATH; using paramiko fallback")
    _paramiko_pull(
        project_dir, profile, dry_run=dry_run, delete=delete, extra_excludes=extra_excludes
    )


def _paramiko_client(profile: Profile):
    try:
        import paramiko
    except ImportError as exc:
        raise MissingDependencyError(
            "paramiko required for fallback sync; install paramiko"
        ) from exc
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(hostname=profile.host)
    return client


def _paramiko_push(
    project_dir: Path,
    profile: Profile,
    *,
    dry_run: bool,
    delete: bool,
    extra_excludes: list[str],
) -> None:
    plans = plan_push_transfers(
        project_dir,
        exclude_from=_rccignore_path(project_dir),
        extra_excludes=extra_excludes,
    )
    if dry_run:
        for plan in plans:
            log.info("would transfer %s", plan.relative_path)
        return

    matcher = build_matcher(_rccignore_path(project_dir), extra_excludes)
    client = _paramiko_client(profile)
    try:
        sftp = client.open_sftp()
        try:
            _sftp_mkdir_p(sftp, profile.remote_dir)
            for plan in plans:
                remote_path = f"{profile.remote_dir.rstrip('/')}/{plan.relative_path}"
                _sftp_mkdir_p(sftp, remote_path.rsplit("/", 1)[0])
                sftp.put(str(plan.absolute_path), remote_path)
            if delete:
                _sftp_prune(
                    sftp,
                    profile.remote_dir,
                    {plan.relative_path for plan in plans},
                    matcher=matcher,
                )
        finally:
            sftp.close()
    finally:
        client.close()


def _paramiko_pull(
    project_dir: Path,
    profile: Profile,
    *,
    dry_run: bool,
    delete: bool,
    extra_excludes: list[str],
) -> None:
    client = _paramiko_client(profile)
    try:
        sftp = client.open_sftp()
        try:
            plans = plan_pull_transfers(
                sftp,
                profile.remote_dir,
                exclude_from=_rccignore_path(project_dir),
                extra_excludes=extra_excludes,
            )
            if dry_run:
                for plan in plans:
                    log.info("would download %s", plan.relative_path)
                return
            for plan in plans:
                local_target = project_dir / plan.relative_path
                local_target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(
                    f"{profile.remote_dir.rstrip('/')}/{plan.relative_path}", str(local_target)
                )
            if delete:
                keep = {plan.relative_path for plan in plans}
                matcher = build_matcher(_rccignore_path(project_dir), extra_excludes)
                _local_prune(project_dir, keep, matcher=matcher)
        finally:
            sftp.close()
    finally:
        client.close()


def _sftp_mkdir_p(sftp, path: str) -> None:
    if not path:
        return
    absolute = path.startswith("/")
    current = "/" if absolute else "."
    for part in [item for item in path.split("/") if item]:
        current = f"{current.rstrip('/')}/{part}" if absolute else f"{current.rstrip('/')}/{part}"
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def _sftp_prune(sftp, remote_root: str, keep: set[str], *, matcher) -> None:
    import stat

    root = remote_root.rstrip("/")

    def walk(prefix: str) -> None:
        base = f"{root}/{prefix}" if prefix else root
        for entry in sftp.listdir_attr(base):
            rel = f"{prefix}/{entry.filename}" if prefix else entry.filename
            full = f"{root}/{rel}"
            if stat.S_ISDIR(entry.st_mode):
                if matcher.match(rel, is_dir=True):
                    continue
                walk(rel)
                try:
                    sftp.rmdir(full)
                except OSError:
                    pass
            elif rel not in keep and not matcher.match(rel, is_dir=False):
                sftp.remove(full)

    walk("")


def _local_prune(project_dir: Path, keep: set[str], *, matcher) -> None:
    for path in sorted(project_dir.rglob("*"), reverse=True):
        rel = path.relative_to(project_dir).as_posix()
        if rel.startswith(".rcc/") or rel == ".rcc":
            continue
        if matcher.match(rel, is_dir=path.is_dir()):
            continue
        if path.is_file() and rel not in keep:
            path.unlink()
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
