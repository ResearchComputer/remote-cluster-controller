from __future__ import annotations

from unittest.mock import patch

import pytest

from rcc import bg
from rcc.config import Profile
from rcc.errors import RemoteError


def make_profile(**kw) -> Profile:
    base = {"host": "myhost", "remote_dir": "/srv/app"}
    base.update(kw)
    return Profile(**base)


# ------------------------------- naming ------------------------------------- #


def test_sanitize_replaces_dot_and_colon():
    assert bg.sanitize("sweep.3") == "sweep-3"
    assert bg.sanitize("a:b") == "a-b"


def test_sanitize_strips_slashes():
    assert bg.sanitize("/jobs/x/") == "jobs/x"


def test_sanitize_rejects_empty():
    with pytest.raises(ValueError):
        bg.sanitize("///")


def test_session_and_paths_use_prefix_and_rundir():
    p = make_profile()
    assert bg.session("run1") == "rcc-run1"
    assert bg.log_path(p, "run1") == "/srv/app/.rcc-runs/run1.log"
    assert bg.status_path(p, "run1") == "/srv/app/.rcc-runs/run1.status"


def test_resolve_cwd_absolute_wins():
    assert bg.resolve_cwd("/srv/app", "/abs") == "/abs"
    assert bg.resolve_cwd("/srv/app", None) == "/srv/app"
    assert bg.resolve_cwd("/srv/app", "sub") == "/srv/app/sub"


# ------------------------------- start argv --------------------------------- #


def test_start_argv_shape_and_quoting():
    p = make_profile()
    argv = bg.start_argv(
        p, "sweep.3", ["python", "train.py", "--x", "a b"], env={"E": "1"}, cwd="run"
    )
    assert argv[0] == "tmux"
    assert "-d" in argv
    assert "-s" in argv
    sess_idx = argv.index("-s") + 1
    assert argv[sess_idx] == "rcc-sweep-3"  # sanitized
    # bash -lc carries the inner wrapper as a single token
    assert "bash" in argv and "-lc" in argv
    inner = argv[-1]
    # env exported, cwd resolved relative to remote_dir, command per-token quoted
    assert "export E=1;" in inner
    assert "cd -- /srv/app/run;" in inner
    assert "python train.py --x 'a b'" in inner
    # tee to the sanitized log path + status capture via PIPESTATUS
    assert "/srv/app/.rcc-runs/sweep-3.log" in inner
    assert "tee -- " in inner
    assert "PIPESTATUS[0]" in inner
    assert "/srv/app/.rcc-runs/sweep-3.status" in inner


def test_start_script_argv_keeps_snippet_verbatim():
    p = make_profile()
    argv = bg.start_script_argv(p, "r", "a && b | grep x")
    inner = argv[-1]
    # the raw snippet survives unquoted inside the subshell
    assert "( set -euo pipefail; a && b | grep x )" in inner


# ------------------------------- list/parse --------------------------------- #


def test_list_sessions_script_uses_format_and_suppresses_errors():
    s = bg.list_sessions_script()
    assert "tmux list-sessions" in s
    assert "session_name" in s
    assert "2>/dev/null" in s
    assert "|| true" in s


def test_parse_sessions_filters_prefix_and_fields():
    out = (
        "rcc-run1\tTue Jun 28 10:00:00 2026\t0\n"
        "other-session\tTue Jun 28 10:00:00 2026\t1\n"
        "rcc-sweep-3\tTue Jun 28 11:00:00 2026\t1\n"
    )
    infos = bg.parse_sessions(out)
    assert [i.name for i in infos] == ["run1", "sweep-3"]
    assert infos[1].attached == "1"


def test_parse_sessions_empty():
    assert bg.parse_sessions("") == []
    assert bg.parse_sessions("no sessions\n") == []


# ------------------------------- misc argv ---------------------------------- #


def test_has_kill_attach_log_argv_use_session_name():
    p = make_profile()
    assert bg.has_session_argv("r!") == ["tmux", "has-session", "-t", "rcc-r!"]
    assert bg.kill_session_argv("r!") == ["tmux", "kill-session", "-t", "rcc-r!"]
    assert bg.attach_argv("r!") == ["tmux", "attach", "-t", "rcc-r!"]
    assert bg.log_tail_argv(p, "r!", follow=True) == [
        "tail",
        "-f",
        "--",
        "/srv/app/.rcc-runs/r!.log",
    ]
    assert bg.log_tail_argv(p, "r!", follow=False)[:3] == ["tail", "-n", "200"]


# ------------------------------- ssh-backed ops ----------------------------- #


def test_ensure_tmux_ok():
    with patch("rcc.bg.run_remote_capture") as cap:
        cap.return_value.returncode = 0
        bg.ensure_tmux(make_profile(host="node"))


def test_ensure_tmux_raises_when_missing():
    with patch("rcc.bg.run_remote_capture") as cap:
        cap.return_value.returncode = 1
        with pytest.raises(RemoteError) as ei:
            bg.ensure_tmux(make_profile(host="node"))
        assert ei.value.exit_code == 127
        assert "tmux" in str(ei.value)


def test_start_returns_sanitized_name():
    with patch("rcc.bg.ensure_tmux"), patch("rcc.bg.run_remote_capture") as cap:
        cap.return_value.returncode = 0
        name = bg.start(make_profile(), "sweep.3", ["python", "t.py"])
        assert name == "sweep-3"
        sent = cap.call_args.args[1]
        assert sent[0] == "tmux"
        assert "rcc-sweep-3" in sent


def test_start_raises_on_tmux_failure():
    with patch("rcc.bg.ensure_tmux"), patch("rcc.bg.run_remote_capture") as cap:
        cap.return_value.returncode = 1
        cap.return_value.stderr = "duplicate session: rcc-x"
        with pytest.raises(RemoteError, match="duplicate session"):
            bg.start(make_profile(), "x", ["true"])


def test_has_session_true_false():
    with patch("rcc.bg.run_remote_capture") as cap:
        cap.return_value.returncode = 0
        assert bg.has_session(make_profile(), "r") is True
        cap.return_value.returncode = 1
        assert bg.has_session(make_profile(), "r") is False


def test_read_status_parses_int():
    with patch("rcc.bg.run_remote_capture") as cap:
        cap.return_value.returncode = 0
        cap.return_value.stdout = "42"
        assert bg.read_status(make_profile(), "r") == 42


def test_read_status_missing_returns_none():
    with patch("rcc.bg.run_remote_capture") as cap:
        cap.return_value.returncode = 1  # cat failed (no status file)
        assert bg.read_status(make_profile(), "r") is None


def test_read_status_garbage_returns_none():
    with patch("rcc.bg.run_remote_capture") as cap:
        cap.return_value.returncode = 0
        cap.return_value.stdout = "not-a-number"
        assert bg.read_status(make_profile(), "r") is None


def test_wait_returns_status_when_session_ends():
    # First has_session -> True, second -> False; then read_status -> 7.
    with (
        patch("rcc.bg.has_session", side_effect=[True, False]),
        patch("rcc.bg.read_status", return_value=7),
        patch("rcc.bg.time.sleep"),
    ):
        assert bg.wait(make_profile(), "r", poll=0) == 7


def test_wait_timeout_returns_none():
    with (
        patch("rcc.bg.has_session", return_value=True),
        patch("rcc.bg.read_status"),
        patch("rcc.bg.time.monotonic", side_effect=[0.0, 10.0]),
    ):
        assert bg.wait(make_profile(), "r", timeout=5, poll=0) is None


def test_resolve_name_uses_sole_session():
    p = make_profile()
    with patch("rcc.bg.list_sessions", return_value=[bg.SessionInfo("only", "x", "0")]):
        assert bg.resolve_name(p, None) == "only"


def test_resolve_name_requires_name_when_multiple():
    p = make_profile()
    with patch(
        "rcc.bg.list_sessions",
        return_value=[bg.SessionInfo("a", "x", "0"), bg.SessionInfo("b", "x", "0")],
    ):
        with pytest.raises(RemoteError, match="multiple sessions"):
            bg.resolve_name(p, None)


def test_resolve_name_errors_when_none():
    p = make_profile()
    with patch("rcc.bg.list_sessions", return_value=[]):
        with pytest.raises(RemoteError, match="no running"):
            bg.resolve_name(p, None)
