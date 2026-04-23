from __future__ import annotations


class RccError(Exception):
    """Base class for all rcc errors."""


class ConfigError(RccError):
    """Missing or malformed .rcc/ configuration."""


class MissingDependencyError(RccError):
    """A required transport is unavailable."""


class RemoteError(RccError):
    """A remote ssh/rsync call failed; exit_code carries the remote exit."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code
