from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from rcc._rsync import (
    DryRunSummary,
    build_rsync_argv,
    classify_dry_run_output,
    format_dry_run_summary,
)
from rcc.config import Profile
from rcc.errors import ConfigError, MissingDependencyError
from rcc.sync import _normalize_paths, pull, push


def make_profile(**kw) -> Profile:
    base = {"host": "h", "remote_dir": "/srv/app"}
    base.update(kw)
    return Profile(**base)


def _ignore(tmp_path: Path) -> Path:
    ig = tmp_path / "rccignore"
    ig.write_text("logs/\n")
    return ig


# --------------------------- build_rsync_argv -------------------------------- #


def test_build_argv_mirror_adds_delete_excluded(tmp_path: Path):
    argv = build_rsync_argv(
        source=tmp_path,
        destination="h:/srv/app",
        e_string="ssh",
        exclude_from=_ignore(tmp_path),
        extra_excludes=[],
        mirror=True,
    )
    assert "--delete" in argv
    assert "--delete-excluded" in argv


def test_build_argv_delete_only_is_bounded(tmp_path: Path):
    argv = build_rsync_argv(
        source=tmp_path,
        destination="h:/srv/app",
        e_string="ssh",
        exclude_from=_ignore(tmp_path),
        extra_excludes=[],
        delete=True,
    )
    assert "--delete" in argv
    assert "--delete-excluded" not in argv


def test_build_argv_keep_remote_emits_protect_filters(tmp_path: Path):
    argv = build_rsync_argv(
        source=tmp_path,
        destination="h:/srv/app",
        e_string="ssh",
        exclude_from=_ignore(tmp_path),
        extra_excludes=[],
        keep_remote=["logs/", "*.safetensors"],
        delete=True,
    )
    assert "--filter=protect logs/" in argv
    assert "--filter=protect *.safetensors" in argv


def test_build_argv_no_ignore_drops_exclude_from(tmp_path: Path):
    argv = build_rsync_argv(
        source=tmp_path,
        destination="h:/srv/app",
        e_string="ssh",
        exclude_from=_ignore(tmp_path),
        extra_excludes=[],
        no_ignore=True,
    )
    assert not any(a.startswith("--exclude-from=") for a in argv)


def test_build_argv_includes_emitted(tmp_path: Path):
    argv = build_rsync_argv(
        source=tmp_path,
        destination="h:/srv/app",
        e_string="ssh",
        exclude_from=_ignore(tmp_path),
        extra_excludes=[],
        includes=["*.bin"],
    )
    assert "--include=*.bin" in argv


def test_build_argv_source_trailing_slash_toggle(tmp_path: Path):
    argv = build_rsync_argv(
        source=tmp_path / "sub",
        destination="h:/srv/app/sub",
        e_string="ssh",
        exclude_from=_ignore(tmp_path),
        extra_excludes=[],
        source_trailing_slash=False,
    )
    # source should NOT gain a trailing slash (scoped: copy the named item)
    assert str(tmp_path / "sub") == argv[-2]
    argv2 = build_rsync_argv(
        source=tmp_path / "sub",
        destination="h:/srv/app/sub",
        e_string="ssh",
        exclude_from=_ignore(tmp_path),
        extra_excludes=[],
    )
    assert argv2[-2] == str(tmp_path / "sub") + "/"


# --------------------------- dry-run classification ------------------------- #


def test_classify_dry_run_output_splits_kinds():
    output = "\n".join(
        [
            "*deleting   logs/old.txt",
            ">f+++++++++ jobs/run.sh",
            "<f.st...... results/out.txt",
            ".f         unchanged.txt",
            "sending incremental file list",
            "",
        ]
    )
    summary = classify_dry_run_output(output)
    assert summary.deletions == ["logs/old.txt"]
    # transfers collapse send/recv (direction is decided by rcc, not parsed)
    assert summary.transfers == ["jobs/run.sh", "results/out.txt"]


def test_format_dry_run_summary_deletions_section_is_distinct():
    summary = DryRunSummary(deletions=["logs/old.txt"], transfers=["a.py"])
    text = format_dry_run_summary(summary, label="/srv/app/", direction="upload")
    # deletions live in their own clearly-named section (issue #2 P1)
    assert "Would DELETE:" in text
    assert "  - logs/old.txt" in text
    assert "Would SEND (upload):" in text
    assert "  + a.py" in text
    assert text.index("Would DELETE") < text.index("Would SEND")


def test_format_dry_run_summary_download_direction():
    summary = DryRunSummary(transfers=["out.txt"])
    text = format_dry_run_summary(summary, label="x", direction="download")
    assert "Would RECEIVE (download):" in text


def test_format_dry_run_summary_no_changes():
    text = format_dry_run_summary(DryRunSummary(), label="/srv/app/")
    assert "no changes" in text


# --------------------------- path normalization ----------------------------- #


def test_normalize_paths_strips_trailing_slash(tmp_path: Path):
    assert _normalize_paths(["jobs/sweep/"], base=tmp_path) == ["jobs/sweep"]


def test_normalize_paths_rejects_dotdot(tmp_path: Path):

    with pytest.raises(ConfigError, match="outside the project"):
        _normalize_paths(["../etc"], base=tmp_path)


def test_normalize_paths_rejects_absolute(tmp_path: Path):

    with pytest.raises(ConfigError, match="outside the project"):
        _normalize_paths(["/etc/passwd"], base=tmp_path)


# --------------------------- scoped push/pull argv --------------------------- #


def test_scoped_push_builds_per_path_rsync(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    (rcc_project / "jobs" / "sweep").mkdir(parents=True)
    (rcc_project / "jobs" / "sweep" / "out.txt").write_text("x")
    captured: list[list[str]] = []

    def fake_dry_run(argv):
        captured.append(argv)
        return DryRunSummary()

    with (
        patch("rcc.sync.shutil.which", side_effect=lambda n: f"/usr/bin/{n}"),
        patch("rcc.sync.run_rsync_dry_run", side_effect=fake_dry_run),
    ):
        push(
            project_dir=rcc_project,
            profile=make_profile(),
            dry_run=True,
            delete=False,
            paths=["jobs/sweep"],
        )
    assert len(captured) == 1
    argv = captured[0]
    # source is the subpath with NO trailing slash; dest is remote_dir/jobs
    assert argv[-2].endswith("jobs/sweep")
    assert not argv[-2].endswith("jobs/sweep/")
    assert argv[-1] == "h:/srv/app/jobs/"
    # scoped transfer bypasses rccignore
    assert not any(a.startswith("--exclude-from=") for a in argv)


def test_scoped_pull_builds_per_path_rsync(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    captured: list[list[str]] = []

    def fake_dry_run(argv):
        captured.append(argv)
        return DryRunSummary()

    with (
        patch("rcc.sync.shutil.which", side_effect=lambda n: f"/usr/bin/{n}"),
        patch("rcc.sync.run_rsync_dry_run", side_effect=fake_dry_run),
    ):
        pull(
            project_dir=rcc_project,
            profile=make_profile(),
            dry_run=True,
            delete=False,
            paths=["jobs/sweep"],
        )
    argv = captured[0]
    assert argv[-2] == "h:/srv/app/jobs/sweep"
    assert not any(a.startswith("--exclude-from=") for a in argv)


def test_scoped_push_requires_rsync(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.sync.shutil.which", return_value=None):
        with pytest.raises(MissingDependencyError, match="scoped push"):
            push(
                project_dir=rcc_project,
                profile=make_profile(),
                dry_run=False,
                delete=False,
                paths=["jobs/sweep"],
            )


def test_whole_push_threads_keep_remote_and_mirror(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    captured = {}

    def fake_dry_run(argv):
        captured["argv"] = argv
        return DryRunSummary()

    with (
        patch("rcc.sync.shutil.which", side_effect=lambda n: f"/usr/bin/{n}"),
        patch("rcc.sync.ensure_remote_dir"),
        patch("rcc.sync.run_rsync_dry_run", side_effect=fake_dry_run),
    ):
        push(
            project_dir=rcc_project,
            profile=make_profile(keep_remote=["cache/"]),
            dry_run=True,
            delete=False,
            mirror=True,
            keep_remote=["logs/"],
        )
    argv = captured["argv"]
    assert "--delete-excluded" in argv
    # both profile-level and CLI keep_remote apply
    assert "--filter=protect cache/" in argv
    assert "--filter=protect logs/" in argv


# ----------------------- real-rsync scoped semantics ----------------------- #
# Validates the riskiest part (trailing slash + leaf creation) against a REAL
# rsync invocation using local paths (no SSH). Both paths local => -e is inert.


def test_real_rsync_scoped_push_preserves_subpath(tmp_path: Path):
    from rcc._rsync import build_rsync_argv, run_rsync

    project = tmp_path / "proj"
    (project / "jobs" / "sweep").mkdir(parents=True)
    (project / "jobs" / "sweep" / "out.txt").write_text("result")
    remote = tmp_path / "remote" / "srv" / "app"  # stands in for remote_dir
    remote.mkdir(parents=True)

    rel = "jobs/sweep"
    parent = "jobs"
    remote_parent = remote / parent
    remote_parent.mkdir(parents=True, exist_ok=True)
    argv = build_rsync_argv(
        source=project / rel,
        destination=str(remote_parent),
        e_string="ssh",
        exclude_from=None,
        extra_excludes=[],
        source_trailing_slash=False,
    )
    run_rsync(argv)
    assert (remote / "jobs" / "sweep" / "out.txt").read_text() == "result"


def test_real_rsync_scoped_pull_preserves_subpath(tmp_path: Path):
    from rcc._rsync import build_rsync_argv, run_rsync

    remote = tmp_path / "remote" / "srv" / "app"
    (remote / "jobs" / "sweep").mkdir(parents=True)
    (remote / "jobs" / "sweep" / "out.txt").write_text("result")
    local_root = tmp_path / "proj"
    local_root.mkdir(parents=True)

    rel = "jobs/sweep"
    parent = "jobs"
    local_parent = local_root / parent
    local_parent.mkdir(parents=True, exist_ok=True)
    argv = build_rsync_argv(
        source=str(remote / rel),
        destination=local_parent,
        e_string="ssh",
        exclude_from=None,
        extra_excludes=[],
        source_trailing_slash=False,
    )
    run_rsync(argv)
    assert (local_root / "jobs" / "sweep" / "out.txt").read_text() == "result"


def test_real_rsync_keep_remote_protects_under_mirror(tmp_path: Path):
    """A protect filter must spare a remote file even with --delete-excluded."""
    from rcc._rsync import build_rsync_argv, run_rsync

    local = tmp_path / "local"
    local.mkdir()
    (local / "keep.txt").write_text("a")
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "logs").mkdir()
    (remote / "logs" / "v.txt").write_text("owned-by-job")  # protected
    (remote / "orphan.txt").write_text("gone")  # not protected

    argv = build_rsync_argv(
        source=local,
        destination=remote,
        e_string="ssh",
        exclude_from=None,
        extra_excludes=[],
        mirror=True,
        keep_remote=["logs"],
    )
    run_rsync(argv)
    assert (remote / "logs" / "v.txt").exists()  # keep_remote survived mirror
    assert not (remote / "orphan.txt").exists()  # mirror deleted the non-protected
