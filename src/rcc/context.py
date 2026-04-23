from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class CliOverrides:
    profile: str | None = None
    host: str | None = None
    remote_dir: str | None = None


_overrides: ContextVar[CliOverrides] = ContextVar("rcc_cli_overrides", default=CliOverrides())


def set_cli_overrides(overrides: CliOverrides) -> None:
    _overrides.set(overrides)


def merge_cli_overrides(
    *,
    profile: str | None,
    host: str | None,
    remote_dir: str | None,
) -> CliOverrides:
    global_overrides = _overrides.get()
    return CliOverrides(
        profile=profile or global_overrides.profile,
        host=host or global_overrides.host,
        remote_dir=remote_dir or global_overrides.remote_dir,
    )
