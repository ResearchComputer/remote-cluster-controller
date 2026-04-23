from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from rcc.config import Profile
from rcc.errors import MissingDependencyError, RemoteError
from rcc.ssh import ensure_remote_dir, mux_check, mux_exit, run_remote


def make_profile(**kw) -> Profile:
    base = {"host": "myhost", "remote_dir": "/srv/app"}
    base.update(kw)
    return Profile(**base)


@pytest.fixture(autouse=True)
def _force_primary(monkeypatch):
    monkeypatch.setattr("rcc.ssh.shutil.which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def fake_run():
    with patch("rcc.ssh.subprocess.run") as mocked:
        yield mocked


def test_run_remote_invokes_ssh(fake_run):
    fake_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    code = run_remote(make_profile(), ["ls"], tty=False)
    assert code == 0
    argv = fake_run.call_args.args[0]
    assert argv[0] == "ssh"
    assert argv[-2] == "myhost"
    assert argv[-1].startswith("bash -lc ")
    assert "ls" in argv[-1]


def test_run_remote_quotes_remote_script_as_single_ssh_command(fake_run):
    fake_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    run_remote(make_profile(remote_dir="/srv/my app"), ["printf", "semi;colon"], tty=False)
    argv = fake_run.call_args.args[0]
    assert argv[-2] == "myhost"
    assert argv[-1].startswith("bash -lc ")
    assert "semi;colon" in argv[-1]


def test_run_remote_propagates_nonzero_exit(fake_run):
    fake_run.return_value = subprocess.CompletedProcess(args=[], returncode=42)
    assert run_remote(make_profile(), ["false"]) == 42


def test_run_remote_tty_flag(fake_run):
    fake_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    run_remote(make_profile(), ["ls"], tty=True)
    assert "-t" in fake_run.call_args.args[0]


def test_ensure_remote_dir_mkdirs(fake_run):
    fake_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    ensure_remote_dir(make_profile())
    assert "mkdir -p -- /srv/app" in fake_run.call_args.args[0][-1]


def test_ensure_remote_dir_raises_on_failure(fake_run):
    fake_run.return_value = subprocess.CompletedProcess(args=[], returncode=1)
    with pytest.raises(RemoteError):
        ensure_remote_dir(make_profile())


def test_mux_check_returns_true_when_open(fake_run):
    fake_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    assert mux_check(make_profile()) is True


def test_mux_check_returns_false_when_closed(fake_run):
    fake_run.return_value = subprocess.CompletedProcess(args=[], returncode=1)
    assert mux_check(make_profile()) is False


def test_mux_exit_issues_exit_command(fake_run):
    fake_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    mux_exit(make_profile())
    argv = fake_run.call_args.args[0]
    assert "-O" in argv and "exit" in argv


def test_run_remote_uses_paramiko_when_ssh_missing(monkeypatch):
    monkeypatch.setattr("rcc.ssh.shutil.which", lambda name: None)
    called = {}

    def fake_fallback(profile, argv, *, tty):
        called["profile"] = profile
        called["argv"] = argv
        called["tty"] = tty
        return 0

    monkeypatch.setattr("rcc.ssh._paramiko_run_remote", fake_fallback)
    assert run_remote(make_profile(), ["ls"], tty=True) == 0
    assert called["argv"] == ["ls"]
    assert called["tty"] is True


def test_mux_check_returns_false_when_ssh_missing(monkeypatch):
    monkeypatch.setattr("rcc.ssh.shutil.which", lambda name: None)
    assert mux_check(make_profile()) is False


def test_mux_exit_raises_when_ssh_missing(monkeypatch):
    monkeypatch.setattr("rcc.ssh.shutil.which", lambda name: None)
    with pytest.raises(MissingDependencyError):
        mux_exit(make_profile())
