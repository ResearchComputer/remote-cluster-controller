from pathlib import Path

import pytest

from rcc.config import Profile, find_rcc_dir, load_config, resolve_profile
from rcc.errors import ConfigError


def test_load_config_parses_profiles(rcc_project: Path):
    cfg = load_config(rcc_project / ".rcc" / "config.toml")
    assert cfg.default == "alpha"
    assert cfg.profiles["alpha"].host == "alpha.example.com"
    assert cfg.profiles["alpha"].remote_dir == "/srv/alpha"


def test_load_config_applies_field_defaults(rcc_project: Path):
    cfg = load_config(rcc_project / ".rcc" / "config.toml")
    profile = cfg.profiles["alpha"]
    assert profile.ssh_control_persist == "30m"
    assert profile.ssh_control_dir == "~/.ssh/controlmasters"


def test_load_config_rejects_missing_required_field(tmp_path: Path):
    config = tmp_path / "c.toml"
    config.write_text('default = "a"\n[profiles.a]\nhost = "h"\n')
    with pytest.raises(ConfigError, match="remote_dir"):
        load_config(config)


def test_load_config_rejects_unknown_default(tmp_path: Path):
    config = tmp_path / "c.toml"
    config.write_text('default = "zzz"\n[profiles.a]\nhost = "h"\nremote_dir = "/d"\n')
    with pytest.raises(ConfigError, match="zzz"):
        load_config(config)


def test_load_config_rejects_empty_profiles(tmp_path: Path):
    config = tmp_path / "c.toml"
    config.write_text('default = "a"\n')
    with pytest.raises(ConfigError, match="no profiles"):
        load_config(config)


def test_find_rcc_dir_in_cwd(rcc_project: Path):
    assert find_rcc_dir(rcc_project) == rcc_project / ".rcc"


def test_find_rcc_dir_walks_up(rcc_project: Path):
    sub = rcc_project / "a" / "b"
    sub.mkdir(parents=True)
    assert find_rcc_dir(sub) == rcc_project / ".rcc"


def test_find_rcc_dir_returns_none_when_absent(tmp_path: Path):
    assert find_rcc_dir(tmp_path) is None


def test_resolve_profile_uses_default(rcc_project: Path):
    profile = resolve_profile(rcc_project)
    assert profile.host == "alpha.example.com"
    assert profile.remote_dir == "/srv/alpha"


def test_resolve_profile_named(tmp_path: Path):
    project = tmp_path / "p"
    project.mkdir()
    rcc = project / ".rcc"
    rcc.mkdir()
    (rcc / "config.toml").write_text(
        'default = "alpha"\n'
        '[profiles.alpha]\nhost = "a"\nremote_dir = "/a"\n'
        '[profiles.beta]\nhost = "b"\nremote_dir = "/b"\n'
    )
    profile = resolve_profile(project, profile_name="beta")
    assert profile.host == "b"


def test_resolve_profile_cli_overrides(rcc_project: Path):
    profile = resolve_profile(rcc_project, host_override="other", remote_dir_override="/x")
    assert profile.host == "other"
    assert profile.remote_dir == "/x"


def test_resolve_profile_raises_when_no_rcc_dir(tmp_path: Path):
    with pytest.raises(ConfigError, match="no .rcc/"):
        resolve_profile(tmp_path)


def test_resolve_profile_raises_on_unknown_profile(rcc_project: Path):
    with pytest.raises(ConfigError, match="gamma"):
        resolve_profile(rcc_project, profile_name="gamma")


def test_profile_exports_expected_defaults():
    profile = Profile(host="h", remote_dir="/r")
    assert profile.ssh_control_persist == "30m"
