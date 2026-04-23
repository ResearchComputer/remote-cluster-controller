from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rcc.errors import ConfigError


@dataclass(frozen=True)
class Profile:
    host: str
    remote_dir: str
    ssh_control_persist: str = "30m"
    ssh_control_dir: str = "~/.ssh/controlmasters"


@dataclass(frozen=True)
class Config:
    default: str
    profiles: dict[str, Profile] = field(default_factory=dict)


_REQUIRED = ("host", "remote_dir")


def load_config(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: malformed TOML: {exc}") from exc
    except FileNotFoundError as exc:
        raise ConfigError(f"{path}: not found") from exc

    default = raw.get("default")
    if not isinstance(default, str) or not default:
        raise ConfigError(f"{path}: missing 'default' profile name")

    profiles_raw = raw.get("profiles", {})
    if not isinstance(profiles_raw, dict) or not profiles_raw:
        raise ConfigError(f"{path}: no profiles defined")

    profiles: dict[str, Profile] = {}
    for name, body in profiles_raw.items():
        if not isinstance(name, str) or not isinstance(body, dict):
            raise ConfigError(f"{path}: malformed profile entry")
        profiles[name] = _parse_profile(path, name, body)

    if default not in profiles:
        raise ConfigError(f"{path}: default profile '{default}' not defined")

    return Config(default=default, profiles=profiles)


def _parse_profile(path: Path, name: str, body: dict[str, Any]) -> Profile:
    for required in _REQUIRED:
        if required not in body:
            raise ConfigError(f"{path}: profile '{name}' missing required field '{required}'")
        if not isinstance(body[required], str) or not body[required]:
            raise ConfigError(f"{path}: profile '{name}' field '{required}' must be a string")

    optional: dict[str, str] = {}
    for key in ("ssh_control_persist", "ssh_control_dir"):
        value = body.get(key)
        if value is not None:
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{path}: profile '{name}' field '{key}' must be a string")
            optional[key] = value

    return Profile(host=body["host"], remote_dir=body["remote_dir"], **optional)


def find_rcc_dir(start: Path) -> Path | None:
    """Walk up from start until a .rcc/ directory is found."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    while True:
        candidate = current / ".rcc"
        if candidate.is_dir():
            return candidate
        if current.parent == current:
            return None
        current = current.parent


def resolve_profile(
    start: Path,
    *,
    profile_name: str | None = None,
    host_override: str | None = None,
    remote_dir_override: str | None = None,
) -> Profile:
    rcc_dir = find_rcc_dir(start)
    if rcc_dir is None:
        raise ConfigError("no .rcc/ found (run 'rcc init')")

    cfg = load_config(rcc_dir / "config.toml")
    name = profile_name or cfg.default
    if name not in cfg.profiles:
        raise ConfigError(f"profile '{name}' not defined in {rcc_dir}/config.toml")

    profile = cfg.profiles[name]
    overrides: dict[str, str] = {}
    if host_override:
        overrides["host"] = host_override
    if remote_dir_override:
        overrides["remote_dir"] = remote_dir_override
    return dataclasses.replace(profile, **overrides) if overrides else profile
