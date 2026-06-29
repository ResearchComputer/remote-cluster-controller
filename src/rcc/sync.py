from __future__ import annotations

import logging
import shutil
from pathlib import Path, PurePosixPath

from rcc._paramiko_fallback import build_matcher, plan_pull_transfers, plan_push_transfers
from rcc._rsync import (
    build_rsync_argv,
    format_dry_run_summary,
    run_rsync,
    run_rsync_dry_run,
)
from rcc.config import Profile
from rcc.errors import ConfigError, MissingDependencyError
from rcc.ssh import build_rsync_e_string, ensure_remote_dir, ensure_remote_path

log = logging.getLogger(__name__)


def _rccignore_path(project_dir: Path) -> Path:
    return project_dir / ".rcc" / "rccignore"


def _primary_available() -> bool:
    return shutil.which("ssh") is not None and shutil.which("rsync") is not None


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def push(
    *,
    project_dir: Path,
    profile: Profile,
    dry_run: bool,
    delete: bool,
    mirror: bool = False,
    extra_excludes: list[str] | None = None,
    includes: list[str] | None = None,
    no_ignore: bool = False,
    paths: list[str] | None = None,
    keep_remote: list[str] | None = None,
) -> None:
    extra_excludes = extra_excludes or []
    includes = includes or []
    keep_remote = list((keep_remote or []) + list(profile.keep_remote))
    normalized = _normalize_paths(paths or [], base=project_dir)

    if normalized:
        # Scoped transfers require rsync (the paramiko fallback syncs the whole
        # project only). They intentionally bypass rccignore so a path that the
        # project ignores (e.g. results in jobs/) can still be fetched/pushed.
        if not _primary_available():
            raise MissingDependencyError(
                "scoped push requires rsync on PATH; the paramiko fallback "
                "syncs the whole project only"
            )
        for rel in normalized:
            _push_path(
                project_dir=project_dir,
                profile=profile,
                rel=rel,
                dry_run=dry_run,
                extra_excludes=extra_excludes,
                includes=includes,
                keep_remote=keep_remote,
            )
        return

    if _primary_available():
        ensure_remote_dir(profile)
        exclude_from = None if no_ignore else _rccignore_path(project_dir)
        argv = build_rsync_argv(
            source=project_dir,
            destination=f"{profile.host}:{profile.remote_dir}",
            e_string=build_rsync_e_string(profile),
            exclude_from=exclude_from,
            extra_excludes=extra_excludes,
            includes=includes,
            dry_run=dry_run,
            delete=delete,
            mirror=mirror,
            keep_remote=keep_remote,
        )
        _invoke(argv, dry_run=dry_run, label=f"{profile.remote_dir}/", direction="upload")
        return

    log.warning("rsync/ssh not on PATH; using paramiko fallback")
    _paramiko_push(
        project_dir,
        profile,
        dry_run=dry_run,
        delete=delete,
        mirror=mirror,
        extra_excludes=extra_excludes,
        includes=includes,
        no_ignore=no_ignore,
        keep_remote=keep_remote,
    )


def pull(
    *,
    project_dir: Path,
    profile: Profile,
    dry_run: bool,
    delete: bool,
    mirror: bool = False,
    extra_excludes: list[str] | None = None,
    includes: list[str] | None = None,
    no_ignore: bool = False,
    paths: list[str] | None = None,
    local_dest: Path | None = None,
    keep_remote: list[str] | None = None,
) -> None:
    extra_excludes = extra_excludes or []
    includes = includes or []
    keep_remote = list((keep_remote or []) + list(profile.keep_remote))
    normalized = _normalize_paths(paths or [], base=project_dir, check_local=False)

    if normalized:
        if not _primary_available():
            raise MissingDependencyError(
                "scoped pull requires rsync on PATH; the paramiko fallback "
                "syncs the whole project only"
            )
        dest_root = local_dest or project_dir
        for rel in normalized:
            _pull_path(
                profile=profile,
                rel=rel,
                dest_root=dest_root,
                dry_run=dry_run,
                extra_excludes=extra_excludes,
                includes=includes,
                keep_remote=keep_remote,
            )
        return

    if _primary_available():
        argv = build_rsync_argv(
            source=f"{profile.host}:{profile.remote_dir}",
            destination=project_dir,
            e_string=build_rsync_e_string(profile),
            exclude_from=None if no_ignore else _rccignore_path(project_dir),
            extra_excludes=extra_excludes,
            includes=includes,
            dry_run=dry_run,
            delete=delete,
            mirror=mirror,
            keep_remote=keep_remote,
        )
        _invoke(argv, dry_run=dry_run, label=f"{profile.remote_dir}/", direction="download")
        return

    log.warning("rsync/ssh not on PATH; using paramiko fallback")
    _paramiko_pull(
        project_dir,
        profile,
        dry_run=dry_run,
        delete=delete,
        mirror=mirror,
        extra_excludes=extra_excludes,
        includes=includes,
        no_ignore=no_ignore,
        keep_remote=keep_remote,
    )


# --------------------------------------------------------------------------- #
# Scoped (per-path) rsync transfers
# --------------------------------------------------------------------------- #


def _push_path(
    *,
    project_dir: Path,
    profile: Profile,
    rel: str,
    dry_run: bool,
    extra_excludes: list[str],
    includes: list[str],
    keep_remote: list[str],
) -> None:
    local_source = project_dir / rel
    if not local_source.exists():
        raise ConfigError(f"path not found locally: {rel}")
    parent = PurePosixPath(rel).parent.as_posix()
    remote_parent = (
        f"{profile.remote_dir.rstrip('/')}/{parent}"
        if parent and parent != "."
        else profile.remote_dir
    )
    # rsync creates the leaf only if its parent exists (verified), so ensure it.
    if not dry_run:
        ensure_remote_path(profile, remote_parent)
    argv = build_rsync_argv(
        source=local_source,
        destination=f"{profile.host}:{remote_parent}",
        e_string=build_rsync_e_string(profile),
        exclude_from=None,  # scoped transfers bypass rccignore by design
        extra_excludes=extra_excludes,
        includes=includes,
        dry_run=dry_run,
        delete=False,  # scoped push is additive; never mirror a subtree
        mirror=False,
        keep_remote=keep_remote,
        source_trailing_slash=False,  # copy the named item, preserving rel path
    )
    _invoke(
        argv, dry_run=dry_run, label=f"{profile.remote_dir.rstrip('/')}/{rel}", direction="upload"
    )


def _pull_path(
    *,
    profile: Profile,
    rel: str,
    dest_root: Path,
    dry_run: bool,
    extra_excludes: list[str],
    includes: list[str],
    keep_remote: list[str],
) -> None:
    parent = PurePosixPath(rel).parent.as_posix()
    local_parent = dest_root / parent if parent and parent != "." else dest_root
    if not dry_run:
        local_parent.mkdir(parents=True, exist_ok=True)
    remote_source = f"{profile.host}:{profile.remote_dir.rstrip('/')}/{rel}"
    argv = build_rsync_argv(
        source=remote_source,
        destination=local_parent,
        e_string=build_rsync_e_string(profile),
        exclude_from=None,
        extra_excludes=extra_excludes,
        includes=includes,
        dry_run=dry_run,
        delete=False,
        mirror=False,
        keep_remote=keep_remote,
        source_trailing_slash=False,
    )
    _invoke(
        argv, dry_run=dry_run, label=f"{profile.remote_dir.rstrip('/')}/{rel}", direction="download"
    )


# --------------------------------------------------------------------------- #
# rsync invocation + dry-run summary
# --------------------------------------------------------------------------- #


def _invoke(argv: list[str], *, dry_run: bool, label: str, direction: str) -> None:
    if dry_run:
        summary = run_rsync_dry_run(argv)
        print(format_dry_run_summary(summary, label=label, direction=direction))
        return
    run_rsync(argv)


def _normalize_paths(paths: list[str], *, base: Path, check_local: bool = True) -> list[str]:
    """Validate user-supplied relative paths; reject escapes (``..``/absolute)."""
    normalized: list[str] = []
    for raw in paths:
        raw_clean = raw.replace("\\", "/")
        pure = PurePosixPath(raw_clean)
        if pure.is_absolute() or any(part == ".." for part in pure.parts):
            raise ConfigError(
                f"refusing path outside the project: {raw!r} (use a relative subpath)"
            )
        rel = raw_clean.strip("/")
        if not rel:
            raise ConfigError("empty path")
        if check_local:
            resolved = (base / rel).resolve()
            try:
                resolved.relative_to(base.resolve())
            except ValueError as exc:
                raise ConfigError(f"refusing path outside the project: {raw!r}") from exc
        normalized.append(rel)
    return normalized


# --------------------------------------------------------------------------- #
# paramiko fallback (whole-project only)
# --------------------------------------------------------------------------- #


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
    mirror: bool,
    extra_excludes: list[str],
    includes: list[str],
    no_ignore: bool,
    keep_remote: list[str],
) -> None:
    exclude_from = None if no_ignore else _rccignore_path(project_dir)
    plans = plan_push_transfers(
        project_dir,
        exclude_from=exclude_from,
        extra_excludes=extra_excludes,
        includes=includes,
    )
    if dry_run:
        for plan in plans:
            log.info("would transfer %s", plan.relative_path)
        return

    matcher = build_matcher(
        exclude_from,
        extra_excludes,
        includes,
    )
    client = _paramiko_client(profile)
    try:
        sftp = client.open_sftp()
        try:
            _sftp_mkdir_p(sftp, profile.remote_dir)
            for plan in plans:
                remote_path = f"{profile.remote_dir.rstrip('/')}/{plan.relative_path}"
                _sftp_mkdir_p(sftp, remote_path.rsplit("/", 1)[0])
                sftp.put(str(plan.absolute_path), remote_path)
            if delete or mirror:
                _sftp_prune(
                    sftp,
                    profile.remote_dir,
                    {plan.relative_path for plan in plans},
                    matcher=matcher,
                    delete_excluded=mirror,
                    keep_remote=keep_remote,
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
    mirror: bool,
    extra_excludes: list[str],
    includes: list[str],
    no_ignore: bool,
    keep_remote: list[str],
) -> None:
    exclude_from = None if no_ignore else _rccignore_path(project_dir)
    client = _paramiko_client(profile)
    try:
        sftp = client.open_sftp()
        try:
            plans = plan_pull_transfers(
                sftp,
                profile.remote_dir,
                exclude_from=exclude_from,
                extra_excludes=extra_excludes,
                includes=includes,
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
            if delete or mirror:
                keep = {plan.relative_path for plan in plans}
                matcher = build_matcher(
                    exclude_from,
                    extra_excludes,
                    includes,
                )
                _local_prune(
                    project_dir,
                    keep,
                    matcher=matcher,
                    delete_excluded=mirror,
                    keep_remote=keep_remote,
                )
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


def _is_protected(rel: str, keep_remote: list[str]) -> bool:
    import fnmatch

    return any(
        fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(rel, pat + "/*") for pat in keep_remote
    )


def _sftp_prune(
    sftp,
    remote_root: str,
    keep: set[str],
    *,
    matcher,
    delete_excluded: bool = False,
    keep_remote: list[str] | None = None,
) -> None:
    import stat

    keep_remote = keep_remote or []
    root = remote_root.rstrip("/")

    def walk(prefix: str) -> None:
        base = f"{root}/{prefix}" if prefix else root
        for entry in sftp.listdir_attr(base):
            rel = f"{prefix}/{entry.filename}" if prefix else entry.filename
            full = f"{root}/{rel}"
            if _is_protected(rel, keep_remote):
                continue
            if stat.S_ISDIR(entry.st_mode):
                if not delete_excluded and matcher.match(rel, is_dir=True):
                    continue
                walk(rel)
                try:
                    sftp.rmdir(full)
                except OSError:
                    pass
            elif rel not in keep and (delete_excluded or not matcher.match(rel, is_dir=False)):
                sftp.remove(full)

    walk("")


def _local_prune(
    project_dir: Path,
    keep: set[str],
    *,
    matcher,
    delete_excluded: bool = False,
    keep_remote: list[str] | None = None,
) -> None:
    keep_remote = keep_remote or []
    for path in sorted(project_dir.rglob("*"), reverse=True):
        rel = path.relative_to(project_dir).as_posix()
        if rel.startswith(".rcc/") or rel == ".rcc":
            continue
        if _is_protected(rel, keep_remote):
            continue
        if matcher.match(rel, is_dir=path.is_dir()):
            if not delete_excluded:
                continue
        if path.is_file() and rel not in keep:
            path.unlink()
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
