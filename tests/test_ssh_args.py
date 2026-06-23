from rcc.config import Profile
from rcc.ssh import (
    build_remote_command,
    build_remote_shell_command,
    build_rsync_e_string,
    build_ssh_args,
)


def make_profile(**kw) -> Profile:
    base = {"host": "myhost", "remote_dir": "/srv/app"}
    base.update(kw)
    return Profile(**base)


def test_run_mode_basic():
    argv = build_ssh_args(make_profile(), mode="run")
    assert argv[0] == "ssh"
    assert "-o" in argv and "ControlPersist=30m" in argv
    assert "ControlPath=~/.ssh/controlmasters/%C" in argv
    assert "ControlMaster=auto" in argv
    assert "LogLevel=ERROR" in argv
    assert argv[-1] == "myhost"


def test_run_mode_with_tty():
    assert "-t" in build_ssh_args(make_profile(), mode="run", tty=True)


def test_run_mode_without_tty_has_no_dash_t():
    assert "-t" not in build_ssh_args(make_profile(), mode="run", tty=False)


def test_mux_check_mode():
    assert build_ssh_args(make_profile(), mode="mux_check")[-3:] == ["-O", "check", "myhost"]


def test_mux_exit_mode():
    assert build_ssh_args(make_profile(), mode="mux_exit")[-3:] == ["-O", "exit", "myhost"]


def test_respects_custom_persist_and_dir():
    argv = build_ssh_args(
        make_profile(ssh_control_persist="1h", ssh_control_dir="/tmp/ctl"), mode="run"
    )
    assert "ControlPersist=1h" in argv
    assert "ControlPath=/tmp/ctl/%C" in argv


def test_rsync_e_string_contains_mux_options():
    e_string = build_rsync_e_string(make_profile())
    assert e_string.startswith("ssh ")
    assert "ControlPath=~/.ssh/controlmasters/%C" in e_string
    assert "ControlMaster=auto" in e_string


def test_build_remote_command_wraps_with_prefix_and_quotes():
    cmd = build_remote_command("/srv/my app", ["ls", "-la", "with space"])
    assert "set -euo pipefail" in cmd
    assert "cd -- '/srv/my app'" in cmd
    assert "'with space'" in cmd


def test_build_remote_command_no_quoting_needed():
    cmd = build_remote_command("/srv/app", ["ls"])
    assert "cd -- /srv/app" in cmd
    assert "ls" in cmd


def test_build_remote_shell_command_passes_script_verbatim():
    # The whole reason -s/--shell exists (issue #1): pipelines, $vars, and
    # nested quotes must survive unquoted so the remote shell interprets them.
    cmd = build_remote_shell_command("/srv/app", "squeue -u $USER | head")
    assert "set -euo pipefail" in cmd
    assert "cd -- /srv/app" in cmd
    assert "squeue -u $USER | head" in cmd
    # the snippet must NOT be collapsed into a single quoted token
    assert "'squeue -u $USER | head'" not in cmd
