from __future__ import annotations

from unittest.mock import patch

import pytest

from rcc import slurm
from rcc.config import Profile
from rcc.errors import RemoteError


def make_profile(**kw) -> Profile:
    base = {"host": "myhost", "remote_dir": "/srv/app"}
    base.update(kw)
    return Profile(**base)


def test_parse_job_id_extracts_digits():
    assert slurm.parse_job_id("Submitted batch job 524614\n") == "524614"


def test_parse_job_id_returns_none_when_unrecognized():
    assert slurm.parse_job_id("some other output") is None


def test_submit_argv_no_env():
    assert slurm.submit_argv("job.sh", []) == ["sbatch", "job.sh"]


def test_submit_argv_with_env():
    argv = slurm.submit_argv("job.sh", [("FOO", "bar"), ("X", "1")])
    assert argv[0] == "sbatch"
    assert "--export=ALL,FOO=bar,X=1" in argv
    assert argv[-1] == "job.sh"


def test_submit_argv_wait():
    assert slurm.submit_argv("job.sh", [], wait=True) == ["sbatch", "--wait", "job.sh"]


def test_submit_argv_dependency():
    argv = slurm.submit_argv("job.sh", [], dependency="afterok:12345")
    assert "--dependency=afterok:12345" in argv
    assert "--wait" not in argv


def test_submit_argv_combined_order():
    # flags precede the script; dependency before wait — locked so future edits stay sane.
    argv = slurm.submit_argv(
        "job.sh", [("E", "2")], wait=True, dependency="afterok:1"
    )
    assert argv == [
        "sbatch",
        "--export=ALL,E=2",
        "--dependency=afterok:1",
        "--wait",
        "job.sh",
    ]


def test_squeue_script_uses_user_and_fixed_format():
    s = slurm.squeue_script()
    assert "id -un" in s
    assert "--format=" in s
    assert slurm.SQUEUE_FORMAT in s


def test_sacct_argv_uses_jobid_and_format():
    assert slurm.sacct_argv("524614") == [
        "sacct",
        "-j",
        "524614",
        f"--format={slurm.SACCT_FORMAT}",
    ]


def test_cancel_argv():
    assert slurm.cancel_argv("42") == ["scancel", "42"]


def test_tail_argv_default_no_follow():
    assert slurm.tail_argv("7", follow=False, filename=None) == [
        "tail",
        "-n",
        "100",
        "slurm-7.out",
    ]


def test_tail_argv_follow():
    assert slurm.tail_argv("7", follow=True, filename=None) == ["tail", "-f", "slurm-7.out"]


def test_tail_argv_filename_override():
    assert slurm.tail_argv("7", follow=True, filename="custom.log") == [
        "tail",
        "-f",
        "custom.log",
    ]


def test_ensure_slurm_ok():
    with patch("rcc.slurm.run_remote_capture") as cap:
        cap.return_value.returncode = 0
        slurm.ensure_slurm(make_profile())  # no raise


def test_ensure_slurm_raises_when_missing():
    with patch("rcc.slurm.run_remote_capture") as cap:
        cap.return_value.returncode = 1
        with pytest.raises(RemoteError) as ei:
            slurm.ensure_slurm(make_profile(host="gpu-cluster"))
        assert ei.value.exit_code == 127
        assert "gpu-cluster" in str(ei.value)


def test_submit_prints_jobid(capsys):
    with patch("rcc.slurm.run_remote_capture") as cap, patch("rcc.slurm.ensure_slurm"):
        cap.return_value.returncode = 0
        cap.return_value.stdout = "Submitted batch job 12345\n"
        cap.return_value.stderr = ""
        assert slurm.submit(make_profile(), "job.sh", []) == 0
        assert "Submitted batch job 12345" in capsys.readouterr().out


def test_submit_surfaces_raw_output_when_unrecognized(capsys):
    with patch("rcc.slurm.run_remote_capture") as cap, patch("rcc.slurm.ensure_slurm"):
        cap.return_value.returncode = 0
        cap.return_value.stdout = "weird output"
        cap.return_value.stderr = ""
        assert slurm.submit(make_profile(), "job.sh", []) == 0
        assert "weird output" in capsys.readouterr().out


def test_submit_passes_extra_env_and_propagates_failure(capsys):
    with patch("rcc.slurm.run_remote_capture") as cap, patch("rcc.slurm.ensure_slurm"):
        cap.return_value.returncode = 1
        cap.return_value.stdout = ""
        cap.return_value.stderr = "boom"
        assert slurm.submit(make_profile(), "job.sh", [("E", "2")]) == 1
        sent = cap.call_args.args[1]  # argv passed to run_remote_capture
        assert "--export=ALL,E=2" in sent
        assert "job.sh" in sent
        assert "boom" in capsys.readouterr().err


def test_submit_dependency_passed_through(capsys):
    with patch("rcc.slurm.run_remote_capture") as cap, patch("rcc.slurm.ensure_slurm"):
        cap.return_value.returncode = 0
        cap.return_value.stdout = "Submitted batch job 12345\n"
        cap.return_value.stderr = ""
        slurm.submit(make_profile(), "job.sh", dependency="afterok:1")
        sent = cap.call_args.args[1]  # argv passed to run_remote_capture
        assert "--dependency=afterok:1" in sent
        assert "--wait" not in sent
        assert "Submitted batch job 12345" in capsys.readouterr().out


def test_submit_wait_streams_and_propagates_exit_code():
    # --wait must stream (run_remote), not capture/parse, and return sbatch's
    # exit code verbatim (== the job's exit code).
    with patch("rcc.slurm.run_remote") as run, patch(
        "rcc.slurm.run_remote_capture"
    ) as cap, patch("rcc.slurm.ensure_slurm"):
        run.return_value = 7
        assert slurm.submit(make_profile(), "job.sh", wait=True) == 7
        sent = run.call_args.args[1]
        assert "--wait" in sent
        cap.assert_not_called()


def test_list_runs_squeue_script():
    with patch("rcc.slurm.run_remote") as run, patch("rcc.slurm.ensure_slurm"):
        run.return_value = 0
        assert slurm.list_jobs(make_profile()) == 0
        assert run.call_args.kwargs["script"] == slurm.squeue_script()


def test_status_runs_sacct():
    with patch("rcc.slurm.run_remote") as run, patch("rcc.slurm.ensure_slurm"):
        run.return_value = 0
        slurm.status(make_profile(), "9")
        assert run.call_args.args[1] == ["sacct", "-j", "9", f"--format={slurm.SACCT_FORMAT}"]


def test_cancel_runs_scancel():
    with patch("rcc.slurm.run_remote") as run, patch("rcc.slurm.ensure_slurm"):
        run.return_value = 0
        slurm.cancel(make_profile(), "9")
        assert run.call_args.args[1] == ["scancel", "9"]


def test_tail_does_not_detect_slurm():
    # `tail` is universal — job tail must not pay for a Slurm detection round-trip.
    with patch("rcc.slurm.run_remote") as run, patch("rcc.slurm.ensure_slurm") as det:
        run.return_value = 0
        slurm.tail(make_profile(), "9", follow=False, filename=None)
        det.assert_not_called()


def test_tail_follow_sets_tty():
    with patch("rcc.slurm.run_remote") as run:
        run.return_value = 0
        slurm.tail(make_profile(), "9", follow=True, filename=None)
        assert run.call_args.kwargs["tty"] is True
        assert run.call_args.args[1] == ["tail", "-f", "slurm-9.out"]
