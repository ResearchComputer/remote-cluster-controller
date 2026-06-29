from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from rcc.cli import app

runner = CliRunner()


# ------------------------------- run --env ---------------------------------- #


def test_run_env_flag_threads_to_remote(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.run.run_remote", return_value=0) as run_fn:
        result = runner.invoke(
            app, ["run", "--env", "EPOCHS=10", "--env", "X=a b", "--", "python", "t.py"]
        )
        assert result.exit_code == 0, result.output
        assert run_fn.call_args.kwargs["env"] == {"EPOCHS": "10", "X": "a b"}
        assert run_fn.call_args.args[1] == ["python", "t.py"]


def test_run_env_layers_over_profile_defaults(tmp_path: Path, monkeypatch):
    project = tmp_path / "p"
    rcc = project / ".rcc"
    rcc.mkdir(parents=True)
    (rcc / "config.toml").write_text(
        'default = "a"\n[profiles.a]\nhost="h"\nremote_dir="/r"\n[profiles.a.env]\nA=1\nB=2\n'
    )
    (rcc / "rccignore").write_text("")
    monkeypatch.chdir(project)
    with patch("rcc.commands.run.run_remote", return_value=0) as run_fn:
        result = runner.invoke(app, ["run", "--env", "B=20", "--env", "C=3", "--", "true"])
        assert result.exit_code == 0, result.output
        # CLI --env wins over profile env; profile defaults preserved otherwise
        assert run_fn.call_args.kwargs["env"] == {"A": "1", "B": "20", "C": "3"}


def test_run_env_file_is_loaded(tmp_path: Path, monkeypatch):
    project = tmp_path / "p"
    rcc = project / ".rcc"
    rcc.mkdir(parents=True)
    (rcc / "config.toml").write_text('default = "a"\n[profiles.a]\nhost="h"\nremote_dir="/r"\n')
    (rcc / "rccignore").write_text("")
    env_file = tmp_path / "g.env"
    env_file.write_text('# comment\nFOO=bar\nexport BAZ="qu ux"\n')
    monkeypatch.chdir(project)
    with patch("rcc.commands.run.run_remote", return_value=0) as run_fn:
        result = runner.invoke(app, ["run", "--env-file", str(env_file), "--", "true"])
        assert result.exit_code == 0, result.output
        assert run_fn.call_args.kwargs["env"] == {"FOO": "bar", "BAZ": "qu ux"}


def test_run_cwd_threads_through(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.run.run_remote", return_value=0) as run_fn:
        result = runner.invoke(app, ["run", "--cwd", "/srv/app/sub", "--", "ls"])
        assert result.exit_code == 0, result.output
        assert run_fn.call_args.kwargs["cwd"] == "/srv/app/sub"


def test_run_bad_env_value(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    result = runner.invoke(app, ["run", "--env", "NOEQUALS", "--", "ls"])
    assert result.exit_code != 0
    assert "KEY=VAL" in result.output


# ------------------------------- config ------------------------------------- #


def test_config_json_is_valid_json(rcc_project: Path, monkeypatch):
    import json

    monkeypatch.chdir(rcc_project)
    result = runner.invoke(app, ["config", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["host"] == "alpha.example.com"
    assert payload["remote_dir"] == "/srv/alpha"


def test_config_get_single_field(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    result = runner.invoke(app, ["config", "--get", "host"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "alpha.example.com"


# ------------------------------- push/pull flags ---------------------------- #


def test_push_mirror_and_keep_remote_plumb_through(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.push._push") as push_fn:
        result = runner.invoke(
            app,
            ["push", "--mirror", "--keep-remote", "logs/", "--include", "*.bin"],
        )
        assert result.exit_code == 0, result.output
        kw = push_fn.call_args.kwargs
        assert kw["mirror"] is True
        assert kw["delete"] is False
        assert kw["keep_remote"] == ["logs/"]
        assert kw["includes"] == ["*.bin"]


def test_push_paths_and_no_ignore_plumb_through(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.push._push") as push_fn:
        result = runner.invoke(app, ["push", "--no-ignore", "jobs/sweep"])
        assert result.exit_code == 0, result.output
        kw = push_fn.call_args.kwargs
        assert kw["paths"] == ["jobs/sweep"]
        assert kw["no_ignore"] is True


def test_push_rejects_delete_and_mirror(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    result = runner.invoke(app, ["push", "--delete", "--mirror"])
    assert result.exit_code != 0


def test_pull_paths_and_local_dest(rcc_project: Path, monkeypatch):
    monkeypatch.chdir(rcc_project)
    with patch("rcc.commands.pull._pull") as pull_fn:
        result = runner.invoke(app, ["pull", "jobs/sweep", "out/"])
        assert result.exit_code == 0, result.output
        kw = pull_fn.call_args.kwargs
        assert kw["paths"] == ["jobs/sweep"]
        assert kw["local_dest"] == Path("out/")


# ------------------------------- tunnel ------------------------------------- #


def test_tunnel_builds_forward_argv_from_profile(tmp_path: Path, monkeypatch):
    project = tmp_path / "p"
    rcc = project / ".rcc"
    rcc.mkdir(parents=True)
    (rcc / "config.toml").write_text(
        'default = "a"\n[profiles.a]\nhost="h"\nremote_dir="/r"\n'
        '[profiles.a.tunnel]\nremote_port=8000\nlocal_port=9000\nremote_host="head"\n'
    )
    (rcc / "rccignore").write_text("")
    monkeypatch.chdir(project)
    captured = {}

    def fake_run(argv):
        captured["argv"] = argv
        raise KeyboardInterrupt

    with patch("rcc.commands.tunnel.subprocess.run", side_effect=fake_run):
        result = runner.invoke(app, ["tunnel"])
    assert result.exit_code == 0, result.output
    assert "9000:head:8000" in captured["argv"]
    assert captured["argv"][-1] == "h"


def test_tunnel_explicit_flags_override_profile(tmp_path: Path, monkeypatch):
    project = tmp_path / "p"
    rcc = project / ".rcc"
    rcc.mkdir(parents=True)
    (rcc / "config.toml").write_text('default = "a"\n[profiles.a]\nhost="h"\nremote_dir="/r"\n')
    (rcc / "rccignore").write_text("")
    monkeypatch.chdir(project)
    captured = {}

    def fake_run(argv):
        captured["argv"] = argv
        raise KeyboardInterrupt

    with patch("rcc.commands.tunnel.subprocess.run", side_effect=fake_run):
        result = runner.invoke(app, ["tunnel", "--remote-port", "80", "--local-port", "8080"])
    assert result.exit_code == 0, result.output
    assert "8080:localhost:80" in captured["argv"]


def test_tunnel_without_port_errors(tmp_path: Path, monkeypatch):
    project = tmp_path / "p"
    rcc = project / ".rcc"
    rcc.mkdir(parents=True)
    (rcc / "config.toml").write_text('default = "a"\n[profiles.a]\nhost="h"\nremote_dir="/r"\n')
    (rcc / "rccignore").write_text("")
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["tunnel"])
    assert result.exit_code != 0
    assert "remote port" in result.output.lower()
