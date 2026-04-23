from __future__ import annotations

from pathlib import Path

import typer

from rcc.errors import ConfigError

_DEFAULT_CONFIG = """\
default = "default"

[profiles.default]
host = "myhost"                # change me (ssh alias or user@host)
remote_dir = "/path/on/remote" # change me
# ssh_control_persist = "30m"
# ssh_control_dir = "~/.ssh/controlmasters"
"""

_DEFAULT_IGNORE = """\
.git/
.venv/
venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
node_modules/
.next/
dist/
build/
"""


def init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing .rcc/ contents"),
) -> None:
    """Scaffold .rcc/config.toml and .rcc/rccignore in the current directory."""
    rcc_dir = Path.cwd() / ".rcc"
    if rcc_dir.exists() and not force:
        raise ConfigError(f"{rcc_dir} already exists (use --force to overwrite)")
    rcc_dir.mkdir(exist_ok=True)
    (rcc_dir / "config.toml").write_text(_DEFAULT_CONFIG)
    (rcc_dir / "rccignore").write_text(_DEFAULT_IGNORE)
    typer.echo(f"Created {rcc_dir}/config.toml and {rcc_dir}/rccignore.")
    typer.echo("Next: edit config.toml to set host and remote_dir, then run 'rcc push --dry-run'.")
    typer.echo("Tip: add '.rcc/' to your .gitignore.")
