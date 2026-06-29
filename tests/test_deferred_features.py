from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from rcc import bg, slurm
from rcc.cli import app
from rcc.config import Profile

runner = CliRunner()


# ------------------------------- run --result-json -------------------------- #


def test_run_result_json_writes_tee_result(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    completed = subprocess.CompletedProcess(
        args=["x"], returncode=0, stdout="hello\n", stderr="warn\n"
    )
    with patch("rcc.commands.run.run_remote_tee", return_value=completed) as tee_fn:
        out = rcc_project / "r.json"
        result = runner.invoke(app, ["run", "--result-json", str(out), "--", "echo", "hi"])
        assert result.exit_code == 0, result.output
        # tee path used (not plain run_remote)
        assert tee_fn.call_args.kwargs["script"] is None
        payload = json.loads(out.read_text())
        assert payload["returncode"] == 0
        assert payload["stdout"] == "hello\n"
        assert payload["stderr"] == "warn\n"
        assert payload["command"] == ["echo", "hi"]


def test_run_result_json_propagates_nonzero_and_still_writes(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    completed = subprocess.CompletedProcess(args=["x"], returncode=3, stdout="", stderr="boom")
    with patch("rcc.commands.run.run_remote_tee", return_value=completed):
        out = rcc_project / "r.json"
        result = runner.invoke(app, ["run", "--result-json", str(out), "--", "false"])
        assert result.exit_code == 3
        payload = json.loads(out.read_text())
        assert payload["returncode"] == 3


# ------------------------------- run --detach ------------------------------- #


def test_run_detach_launches_bg_and_does_not_run_foreground(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with (
        patch("rcc.commands.run.bg.start", return_value="sweep-3") as start_fn,
        patch("rcc.commands.run.run_remote") as run_fn,
    ):
        result = runner.invoke(
            app, ["run", "--detach", "--name", "sweep.3", "--", "python", "t.py"]
        )
        assert result.exit_code == 0, result.output
        assert "Launched detached run 'sweep-3'" in result.output
        start_fn.assert_called_once()
        assert start_fn.call_args.args[2] == ["python", "t.py"]
        assert start_fn.call_args.kwargs["script"] is None
        run_fn.assert_not_called()


def test_run_detach_layers_env_and_passes_script(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.run.bg.start", return_value="r") as start_fn:
        result = runner.invoke(
            app,
            ["run", "--detach", "--name", "r", "--env", "E=1", "-s", "a && b"],
        )
        assert result.exit_code == 0, result.output
        assert start_fn.call_args.kwargs["script"] == "a && b"
        assert start_fn.call_args.kwargs["env"] == {"E": "1"}


def test_run_detach_auto_name_when_unnamed(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.run.bg.start", return_value="auto") as start_fn:
        result = runner.invoke(app, ["run", "--detach", "--", "python", "t.py"])
        assert result.exit_code == 0, result.output
        # the auto-generated name is derived from the first argv token
        assert start_fn.call_args.args[1].startswith("python-")


def test_run_detach_rejects_tty(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    result = runner.invoke(app, ["run", "--detach", "-t", "--", "ls"])
    assert result.exit_code != 0


# ------------------------------- bg subgroup -------------------------------- #


def test_bg_ps_lists_sessions(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.bg.bg.list_sessions") as fn:
        fn.return_value = [
            bg.SessionInfo("run1", "Tue Jun 28 10:00:00 2026", "0"),
            bg.SessionInfo("sweep-3", "Tue Jun 28 11:00:00 2026", "1"),
        ]
        result = runner.invoke(app, ["bg", "ps"])
        assert result.exit_code == 0, result.output
        assert "run1" in result.output and "sweep-3" in result.output


def test_bg_ps_empty_message(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.bg.bg.list_sessions", return_value=[]):
        result = runner.invoke(app, ["bg", "ps"])
        assert result.exit_code == 0
        assert "No running" in result.output


def test_bg_logs_uses_resolved_name_and_follow(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.bg.bg.logs", return_value=0) as fn:
        result = runner.invoke(app, ["bg", "logs", "r", "-f"])
        assert result.exit_code == 0, result.output
        assert fn.call_args.args[1] == "r"
        assert fn.call_args.kwargs["follow"] is True


def test_bg_wait_propagates_exit_code(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.bg.bg.wait", return_value=5):
        result = runner.invoke(app, ["bg", "wait", "r"])
        assert result.exit_code == 5


def test_bg_wait_timeout_exits_124(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.bg.bg.wait", return_value=None):
        result = runner.invoke(app, ["bg", "wait", "r", "--timeout", "5"])
        assert result.exit_code == 124


def test_bg_stop_prints_confirmation(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.bg.bg.stop", return_value=0):
        result = runner.invoke(app, ["bg", "stop", "r"])
        assert result.exit_code == 0, result.output
        assert "Stopped r" in result.output


# ------------------------------- job wait ----------------------------------- #


def test_slurm_parse_outcome_completed():
    o = slurm.parse_outcome("COMPLETED|0:0\n")
    assert o.ok
    assert o.exit_code == 0


def test_slurm_parse_outcome_failed_with_code():
    o = slurm.parse_outcome("FAILED|2:0\n")
    assert not o.ok
    assert o.exit_code == 2


def test_slurm_parse_outcome_empty():
    o = slurm.parse_outcome("")
    assert o.state is None
    assert slurm.outcome_to_exit_code(o) == 1


def test_slurm_outcome_exit_code_completed_is_zero():
    assert slurm.outcome_to_exit_code(slurm.JobOutcome("COMPLETED", 0, "")) == 0


def test_slurm_wait_polls_then_classifies():
    p = Profile(host="h", remote_dir="/r")
    # squeue responses: in queue (RUNNING), then gone (empty). sacct -> FAILED|3:0.
    squeue_results = [
        subprocess.CompletedProcess([], 0, "RUNNING\n", ""),
        subprocess.CompletedProcess([], 0, "", ""),
    ]
    sacct_result = subprocess.CompletedProcess([], 0, "FAILED|3:0\n", "")
    with (
        patch("rcc.slurm.run_remote_capture", side_effect=squeue_results + [sacct_result]),
        patch("rcc.slurm.time.sleep"),
    ):
        assert slurm.wait(p, "123") == 3


def test_slurm_wait_returns_on_target_state():
    p = Profile(host="h", remote_dir="/r")
    running = subprocess.CompletedProcess([], 0, "RUNNING\n", "")
    with (
        patch("rcc.slurm.run_remote_capture", return_value=running),
        patch("rcc.slurm.time.sleep"),
    ):
        assert slurm.wait(p, "123", on="RUNNING") == 0


def test_slurm_wait_timeout_returns_124():
    p = Profile(host="h", remote_dir="/r")
    running = subprocess.CompletedProcess([], 0, "RUNNING\n", "")
    with (
        patch("rcc.slurm.run_remote_capture", return_value=running),
        patch("rcc.slurm.time.sleep"),
        patch("rcc.slurm.time.monotonic", side_effect=[0.0, 100.0]),
    ):
        assert slurm.wait(p, "123", timeout=5, poll=0) == 124


def test_cli_job_wait_propagates_nonzero(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.slurm.wait", return_value=2):
        result = runner.invoke(app, ["job", "wait", "9"])
        assert result.exit_code == 2


def test_cli_job_wait_success(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.slurm.wait", return_value=0) as fn:
        result = runner.invoke(app, ["job", "wait", "9", "--on", "RUNNING"])
        assert result.exit_code == 0, result.output
        assert fn.call_args.kwargs["on"] == "RUNNING"


# ------------------------------- job list/status --json --------------------- #


def test_cli_job_list_json(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    rows = [
        {"JobID": "12345", "Name": "train", "State": "RUNNING", "ok": False},
    ]
    with patch("rcc.slurm.list_jobs_json", return_value=rows):
        result = runner.invoke(app, ["job", "list", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == rows


def test_cli_job_list_json_empty(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.slurm.list_jobs_json", return_value=[]):
        result = runner.invoke(app, ["job", "list", "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []


def test_cli_job_list_text_mode_still_streams(rcc_project: Path, monkeypatch):
    # Without --json, the existing streaming path is used (not list_jobs_json).
    monkeypatch.chdir(rcc_project)
    with (
        patch("rcc.slurm.list_jobs", return_value=0) as stream_fn,
        patch("rcc.slurm.list_jobs_json") as json_fn,
    ):
        result = runner.invoke(app, ["job", "list"])
        assert result.exit_code == 0, result.output
        stream_fn.assert_called_once()
        json_fn.assert_not_called()


def test_cli_job_status_json(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    rows = [{"JobID": "9", "State": "FAILED", "exit_code": 3, "ok": False}]
    with patch("rcc.slurm.status_json", return_value=rows) as fn:
        result = runner.invoke(app, ["job", "status", "9", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == rows
        assert fn.call_args.args[1] == "9"


# ------------------------------- status --json ------------------------------ #


def test_status_json_open(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.status.mux_check", return_value=True):
        result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload == {"controlmaster_open": True, "host": "alpha.example.com"}


def test_status_json_closed_does_not_exit_1(rcc_project: Path, monkeypatch):
    # text mode exits 1 when closed; --json stays 0 so wrappers can parse it.
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.status.mux_check", return_value=False):
        result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["controlmaster_open"] is False
