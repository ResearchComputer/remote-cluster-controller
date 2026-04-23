from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from rcc.cli import app

runner = CliRunner()


def test_init_creates_rcc_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".rcc" / "config.toml").exists()
    assert (tmp_path / ".rcc" / "rccignore").exists()


def test_init_refuses_when_exists(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".rcc").mkdir()
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert ".rcc" in result.output


def test_init_force_overwrites(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".rcc").mkdir()
    result = runner.invoke(app, ["init", "--force"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".rcc" / "config.toml").exists()


def test_cli_push_propagates_flags_and_excludes(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.push._push") as push_fn:
        result = runner.invoke(
            app, ["push", "--dry-run", "--delete", "--exclude", "*.log", "--exclude", "tmp/"]
        )
        assert result.exit_code == 0, result.output
        kwargs = push_fn.call_args.kwargs
        assert kwargs["dry_run"] is True
        assert kwargs["delete"] is True
        assert kwargs["extra_excludes"] == ["*.log", "tmp/"]
        assert kwargs["profile"].host == "alpha.example.com"


def test_cli_push_host_override(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.push._push") as push_fn:
        result = runner.invoke(app, ["push", "--host", "other"])
        assert result.exit_code == 0, result.output
        assert push_fn.call_args.kwargs["profile"].host == "other"


def test_cli_run_passes_argv_through(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.run.run_remote", return_value=0) as run_fn:
        result = runner.invoke(app, ["run", "--", "nvidia-smi", "-L"])
        assert result.exit_code == 0, result.output
        assert run_fn.call_args.args[1] == ["nvidia-smi", "-L"]


def test_cli_run_propagates_nonzero(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.run.run_remote", return_value=42):
        result = runner.invoke(app, ["run", "--", "false"])
        assert result.exit_code == 42


def test_cli_status_no_ssh_exits_1(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.status.shutil.which", return_value=None):
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 1
        assert "not available" in result.output


def _rcc(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", "rcc", *args], cwd=cwd, capture_output=True, text=True)


def test_push_exit_2_when_no_rcc_dir(tmp_path: Path):
    result = _rcc(["push"], cwd=tmp_path)
    assert result.returncode == 2
    assert "no .rcc/" in result.stderr


def test_config_prints_resolved(rcc_project: Path):
    result = _rcc(["config"], cwd=rcc_project)
    assert result.returncode == 0
    assert "alpha.example.com" in result.stdout
    assert "/srv/alpha" in result.stdout


def test_config_host_override(rcc_project: Path):
    result = _rcc(["config", "--host", "other"], cwd=rcc_project)
    assert result.returncode == 0
    assert "other" in result.stdout


def test_config_root_level_host_override(rcc_project: Path):
    result = _rcc(["--host", "other", "config"], cwd=rcc_project)
    assert result.returncode == 0
    assert "other" in result.stdout


def test_run_without_argv_errors(rcc_project: Path):
    result = _rcc(["run"], cwd=rcc_project)
    assert result.returncode != 0
