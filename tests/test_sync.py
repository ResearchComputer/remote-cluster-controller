from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rcc._paramiko_fallback import plan_pull_transfers, plan_push_transfers
from rcc._rsync import DryRunSummary, build_rsync_argv, run_rsync
from rcc.config import Profile
from rcc.errors import RemoteError
from rcc.sync import _local_prune, pull, push


def make_profile(**kw) -> Profile:
    base = {"host": "h", "remote_dir": "/srv/app"}
    base.update(kw)
    return Profile(**base)


def test_build_rsync_argv_push_defaults(tmp_path: Path):
    ignore = tmp_path / "rccignore"
    ignore.write_text(".git/\n")
    argv = build_rsync_argv(
        source=tmp_path,
        destination="h:/srv/app",
        e_string="ssh -o X=y",
        exclude_from=ignore,
        extra_excludes=[],
        dry_run=False,
        delete=False,
    )
    assert argv[0] == "rsync"
    assert "-a" in argv and "-z" in argv
    assert "--partial" in argv
    assert "--human-readable" in argv
    assert "--info=stats2,progress2" in argv
    assert f"--exclude-from={ignore}" in argv
    assert "-e" in argv
    assert "ssh -o X=y" in argv
    assert str(tmp_path) + "/" == argv[-2]
    assert argv[-1] == "h:/srv/app/"


def test_build_rsync_argv_dry_run_and_delete_and_extra(tmp_path: Path):
    ignore = tmp_path / "rccignore"
    ignore.write_text("")
    argv = build_rsync_argv(
        source=tmp_path,
        destination="h:/srv/app",
        e_string="ssh",
        exclude_from=ignore,
        extra_excludes=["*.log", "tmp/"],
        dry_run=True,
        delete=True,
    )
    assert "--dry-run" in argv
    assert "--itemize-changes" in argv
    assert "--delete" in argv
    assert "--exclude=*.log" in argv
    assert "--exclude=tmp/" in argv


def test_run_rsync_raises_on_nonzero():
    with patch("rcc._rsync.subprocess.run") as mocked:
        mocked.return_value = subprocess.CompletedProcess(args=[], returncode=23)
        with pytest.raises(RemoteError) as exc_info:
            run_rsync(["rsync", "-a"])
        assert exc_info.value.exit_code == 23


def test_plan_push_transfers_applies_ignore(tmp_path: Path):
    (tmp_path / "keep.txt").write_text("a")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")
    ignore = tmp_path / "rccignore"
    ignore.write_text(".git/\n")
    plans = plan_push_transfers(tmp_path, exclude_from=ignore, extra_excludes=[])
    assert sorted(plan.relative_path for plan in plans) == ["keep.txt", "rccignore"]


def test_plan_push_transfers_additive_extra_excludes(tmp_path: Path):
    (tmp_path / "a.log").write_text("x")
    (tmp_path / "b.txt").write_text("y")
    ignore = tmp_path / "rccignore"
    ignore.write_text("")
    plans = plan_push_transfers(tmp_path, exclude_from=ignore, extra_excludes=["*.log"])
    assert sorted(plan.relative_path for plan in plans) == ["b.txt", "rccignore"]


def test_plan_pull_transfers_applies_ignore(tmp_path: Path):
    class Entry:
        def __init__(self, filename, st_mode):
            self.filename = filename
            self.st_mode = st_mode

    import stat

    sftp = MagicMock()
    sftp.listdir_attr.side_effect = lambda path: {
        "/remote": [
            Entry("keep.txt", stat.S_IFREG),
            Entry(".git", stat.S_IFDIR),
        ],
        "/remote/.git": [Entry("HEAD", stat.S_IFREG)],
    }[path]
    ignore = tmp_path / "rccignore"
    ignore.write_text(".git/\n")
    plans = plan_pull_transfers(sftp, "/remote", exclude_from=ignore, extra_excludes=[])
    assert [plan.relative_path for plan in plans] == ["keep.txt"]


def test_push_prefers_rsync_when_available(rcc_project: Path):
    with (
        patch("rcc.sync.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"),
        patch("rcc.sync.ensure_remote_dir") as ensure,
        patch("rcc.sync.run_rsync") as run_r,
    ):
        push(
            project_dir=rcc_project,
            profile=make_profile(),
            dry_run=False,
            delete=False,
            extra_excludes=[],
        )
        ensure.assert_called_once()
        run_r.assert_called_once()
        assert run_r.call_args.args[0][0] == "rsync"


def test_push_falls_back_when_rsync_missing(rcc_project: Path):
    with (
        patch("rcc.sync.shutil.which", return_value=None),
        patch("rcc.sync._paramiko_push") as fallback,
    ):
        push(
            project_dir=rcc_project,
            profile=make_profile(),
            dry_run=False,
            delete=False,
            extra_excludes=[],
        )
        fallback.assert_called_once()


def test_pull_prefers_rsync_when_available(rcc_project: Path):
    with (
        patch("rcc.sync.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"),
        patch("rcc.sync.run_rsync_dry_run") as dry,
    ):
        dry.return_value = DryRunSummary()
        pull(
            project_dir=rcc_project,
            profile=make_profile(),
            dry_run=True,
            delete=False,
            extra_excludes=[],
        )
        assert "--dry-run" in dry.call_args.args[0]


def test_local_prune_preserves_ignored_paths(tmp_path: Path):
    keep = tmp_path / "keep.txt"
    keep.write_text("k")
    ignored_dir = tmp_path / ".git"
    ignored_dir.mkdir()
    ignored_file = ignored_dir / "HEAD"
    ignored_file.write_text("ref")
    orphan = tmp_path / "orphan.txt"
    orphan.write_text("x")
    matcher = build_matcher_for_test(tmp_path, [".git/"])

    _local_prune(tmp_path, {"keep.txt"}, matcher=matcher)

    assert keep.exists()
    assert ignored_file.exists()
    assert not orphan.exists()


def build_matcher_for_test(tmp_path: Path, patterns: list[str]):
    ignore = tmp_path / "rccignore"
    ignore.write_text("\n".join(patterns))
    from rcc._paramiko_fallback import build_matcher

    return build_matcher(ignore, [])
