from __future__ import annotations

import subprocess
from pathlib import Path

from rcc.errors import RemoteError


def build_rsync_argv(
    *,
    source: Path | str,
    destination: Path | str,
    e_string: str,
    exclude_from: Path,
    extra_excludes: list[str],
    dry_run: bool,
    delete: bool,
) -> list[str]:
    argv: list[str] = [
        "rsync",
        "-a",
        "-z",
        "--human-readable",
        "--info=stats2,progress2",
        "--partial",
        f"--exclude-from={exclude_from}",
    ]
    argv.extend(f"--exclude={pattern}" for pattern in extra_excludes)
    if dry_run:
        argv.extend(["--dry-run", "--itemize-changes"])
    if delete:
        argv.append("--delete")
    argv.extend(["-e", e_string, _with_trailing_slash(source), _with_trailing_slash(destination)])
    return argv


def _with_trailing_slash(value: Path | str) -> str:
    text = str(value)
    return text if text.endswith("/") else f"{text}/"


def run_rsync(argv: list[str]) -> None:
    result = subprocess.run(argv)
    if result.returncode != 0:
        raise RemoteError(
            f"rsync exited with code {result.returncode}", exit_code=result.returncode
        )
