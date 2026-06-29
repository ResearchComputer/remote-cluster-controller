from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rcc.errors import RemoteError


@dataclass(frozen=True)
class DryRunSummary:
    """Classified view of a ``--dry-run`` rsync, so deletions can't hide.

    The whole point (issue #2 P1) is that ``Would DELETE`` shows up as its own
    section instead of being lost in a wall of ``f.f.....`` itemize lines. We
    do NOT try to infer send-vs-receive from rsync's ``>``/``<`` codes (those
    reflect the sender's side and mislabel local-to-local copies); rcc knows the
    direction (push=upload, pull=download) and passes it to the formatter.
    """

    deletions: list[str] = field(default_factory=list)
    transfers: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.deletions) or bool(self.transfers)


def build_rsync_argv(
    *,
    source: Path | str,
    destination: Path | str,
    e_string: str,
    exclude_from: Path | None,
    extra_excludes: list[str],
    includes: list[str] | None = None,
    no_ignore: bool = False,
    dry_run: bool = False,
    delete: bool = False,
    mirror: bool = False,
    keep_remote: list[str] | None = None,
    source_trailing_slash: bool = True,
) -> list[str]:
    argv: list[str] = [
        "rsync",
        "-a",
        "-z",
        "--human-readable",
        "--info=stats2,progress2",
        "--partial",
    ]
    if exclude_from is not None and not no_ignore:
        argv.append(f"--exclude-from={exclude_from}")
    argv.extend(f"--exclude={pattern}" for pattern in extra_excludes)
    # includes are emitted before the implicit exclude-all of a scoped transfer
    # is meaningful only when paired with --exclude='*' upstream; we emit them
    # here so callers can restrict a scoped push/pull to specific globs.
    argv.extend(f"--include={pattern}" for pattern in (includes or []))
    # Protect-list: even --mirror must spare remote-owned paths (logs/, cache/,
    # *.safetensors). Implemented as rsync protect filters, which survive
    # --delete-excluded (verified). The guard rail lives in the tool (issue #2 P1).
    for pattern in keep_remote or []:
        argv.append(f"--filter=protect {pattern}")
    if mirror:
        # The dangerous full mirror: delete excluded files too.
        argv.append("--delete")
        argv.append("--delete-excluded")
    elif delete:
        # Safer "sync": only delete files inside the (non-excluded) transfer
        # scope that have vanished from the source.
        argv.append("--delete")
    if dry_run:
        argv.extend(["--dry-run", "--itemize-changes"])
    argv.append("-e")
    argv.append(e_string)
    argv.append(
        _with_trailing_slash(source) if source_trailing_slash else _without_extra_slash(source)
    )
    argv.append(_with_trailing_slash(destination))
    return argv


def _without_extra_slash(value: Path | str) -> str:
    """Source path as-is (no trailing slash) — for scoped transfers that copy
    the *named item* rather than its contents."""
    return str(value)


def _with_trailing_slash(value: Path | str) -> str:
    text = str(value)
    return text if text.endswith("/") else f"{text}/"


def run_rsync(argv: list[str]) -> None:
    result = subprocess.run(argv)
    if result.returncode != 0:
        raise RemoteError(
            f"rsync exited with code {result.returncode}", exit_code=result.returncode
        )


def run_rsync_dry_run(argv: list[str]) -> DryRunSummary:
    """Run a dry-run rsync, capturing and classifying its itemized output.

    The caller passes a *dry-run* argv (``build_rsync_argv(..., dry_run=True)``);
    we capture stdout/stderr so we can surface deletions distinctly.
    """
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise RemoteError(
            f"rsync exited with code {result.returncode}", exit_code=result.returncode
        )
    return classify_dry_run_output(result.stdout)


def classify_dry_run_output(output: str) -> DryRunSummary:
    summary = DryRunSummary()
    for line in output.splitlines():
        parsed = _parse_itemize_line(line)
        if parsed is None:
            continue
        kind, path = parsed
        if kind == "delete":
            summary.deletions.append(path)
        elif kind == "transfer":
            summary.transfers.append(path)
    return summary


def format_dry_run_summary(
    summary: DryRunSummary, *, label: str = "", direction: str | None = None
) -> str:
    """Render a dry-run with deletions in their own, unmissable section.

    This is the ``--dry-run`` ergonomics fix (issue #2 P1): deletions must not
    hide in a wall of ``f.f.....`` lines. ``direction`` ("upload"/"download")
    labels the transfer section; rcc knows it from push vs pull.
    """
    header = f"Dry run for {label}" if label else "Dry run"
    lines = [header]
    if not summary.has_changes:
        lines.append("  (no changes)")
        return "\n".join(lines)
    if summary.deletions:
        lines.append("Would DELETE:")
        for path in summary.deletions:
            lines.append(f"  - {path}")
    if summary.transfers:
        verb = {"upload": "SEND (upload)", "download": "RECEIVE (download)"}.get(
            direction or "", "TRANSFER"
        )
        lines.append(f"Would {verb}:")
        for path in summary.transfers:
            lines.append(f"  + {path}")
    return "\n".join(lines)


# rsync itemize-changes lines look like:
#   *deleting   path/to/file
#   >f+++++++++ path/to/file        (file transferred to the receiver)
#   <f.st...... path/to/file        (delta received)
#   .f          path/to/file        (no change / '.' = no update)
#   cd.......   path/               (directory change)
_DELETE_RE = re.compile(r"^\*deleting\s+(.*)$")
# The first code column tells direction: '>'/'<'/'c' = a transfer of some kind,
# '.' = no-op. rsync emits ~11 columns; we anchor on the leading char and treat
# the rest as the code body, then the path. Direction (send/recv) is decided by
# rcc (push vs pull), NOT parsed from '>'/'<' (unreliable for local copies).
_ITEMIZE_RE = re.compile(r"^(?P<code>[<>c.])\S+\s+(?P<path>.+)$")


def _parse_itemize_line(line: str) -> tuple[str, str] | None:
    line = line.rstrip("\n")
    if not line:
        return None
    delete = _DELETE_RE.match(line)
    if delete:
        return "delete", delete.group(1)
    item = _ITEMIZE_RE.match(line)
    if not item:
        return None
    code = item.group("code")
    path = item.group("path")
    if code.startswith("."):
        # '.' leading column = no update; nothing actionable to surface.
        return None
    return "transfer", path
