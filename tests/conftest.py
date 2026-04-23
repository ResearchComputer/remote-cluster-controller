from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def rcc_project(tmp_path: Path) -> Path:
    """A tmp project with a minimal .rcc/config.toml and rccignore."""
    project = tmp_path / "project"
    project.mkdir()
    rcc = project / ".rcc"
    rcc.mkdir()
    (rcc / "config.toml").write_text(
        'default = "alpha"\n'
        "\n"
        "[profiles.alpha]\n"
        'host = "alpha.example.com"\n'
        'remote_dir = "/srv/alpha"\n'
    )
    (rcc / "rccignore").write_text(".git/\n__pycache__/\n")
    return project
