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
    verbose: bool | None = None,
) -> list[str]:
    # The stats2 block is always emitted: even quiet runs parse it into a
    # one-line summary (issue #6). progress2 renders live under --verbose.
    # When verbose, also ask rsync for its own per-file detail so "present all"
    # means the full file list, not just a progress bar.
    if verbose is None:
        from rcc.context import is_verbose

        verbose = is_verbose()
    argv: list[str] = [
        "rsync",
        "-a",
        "-z",
        "--human-readable",
        "--info=stats2,progress2",
        "--partial",
    ]
    if verbose:
        argv.append("-v")
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


def run_rsync(
    argv: list[str],
    *,
    label: str = "",
    direction: str | None = None,
    verbose: bool | None = None,
) -> None:
    """Run a real (non-dry) rsync.

    Output policy (issue #6): by default we stay quiet and surface only a
    one-line summary parsed from rsync's stats block; under ``--verbose`` we get
    out of the way and let rsync stream straight to the terminal so the live
    progress bar and per-file output render as rsync intends.
    """
    if verbose is None:
        from rcc.context import is_verbose

        verbose = is_verbose()
    if verbose:
        result = subprocess.run(argv)
        if result.returncode != 0:
            raise RemoteError(
                f"rsync exited with code {result.returncode}", exit_code=result.returncode
            )
        return
    # Quiet default: capture everything, then report only the essentials.
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise RemoteError(
            _rsync_error_message(result.returncode, result.stderr),
            exit_code=result.returncode,
        )
    print(format_transfer_summary(result.stdout, label=label, direction=direction))


def _rsync_error_message(code: int, stderr: str | None) -> str:
    msg = f"rsync exited with code {code}"
    tail = (stderr or "").strip()
    if tail:
        # rsync reports the cause on stderr; keep the last few meaningful lines
        # so users don't need --verbose just to see why it failed.
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        snippet = "\n".join(lines[-3:])
        msg = f"{msg}:\n{snippet}"
    return msg


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


# --------------------------------------------------------------------------- #
# Real-transfer summary (issue #6): parse rsync's --info=stats2 block into a
# single line so the default push/pull isn't a wall of progress+stats noise.
# --------------------------------------------------------------------------- #
_TRANSFERRED_RE = re.compile(r"Number of regular files transferred:\s*([\d,]+)")
_DELETED_RE = re.compile(r"Number of deleted files:\s*([\d,]+)")
# "sent 132 bytes  received 35 bytes"  (or, with --human-readable, "500.00K bytes")
_BYTES_RE = re.compile(r"sent\s+(\S+)\s+bytes\s+received\s+(\S+)\s+bytes")


def _parse_count(text: str, pattern: re.Pattern[str]) -> int | None:
    m = pattern.search(text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def format_transfer_summary(
    output: str, *, label: str = "", direction: str | None = None
) -> str:
    """Render a real rsync transfer as one concise line.

    ``direction`` ("upload"/"download") picks the verb (Pushed/Pulled) and which
    byte count to highlight — ``sent`` for a push, ``received`` for a pull.
    Falls back gracefully if rsync emitted no stats block at all.
    """
    verb = {"upload": "Pushed", "download": "Pulled"}.get(
        (direction or "").lower(), "Transferred"
    )
    files = _parse_count(output, _TRANSFERRED_RE)
    deleted = _parse_count(output, _DELETED_RE)
    size_token = None
    bytes_match = _BYTES_RE.search(output)
    if bytes_match:
        size_token = bytes_match.group(2) if direction == "download" else bytes_match.group(1)

    bits: list[str] = []
    if files is not None and files != 0:
        # Bytes are only meaningful alongside actual file transfers; on a no-op
        # rsync still sends ~20 bytes of protocol noise we don't want to surface.
        bits.append(f"{files} file{'s' if files != 1 else ''}")
        if size_token is not None:
            unit = "received" if direction == "download" else "sent"
            # size_token is already human-readable ("500.00K"/"132"); append a "B".
            bits.append(f"({size_token}B {unit})")

    if not bits and deleted:
        body = "nothing transferred"
    elif not bits:
        body = "no changes"
    else:
        body = " ".join(bits)

    line = f"{verb} {body}"
    if deleted:
        line += f", deleted {deleted}"
    if label:
        line += f" \u2192 {label}"
    return line


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
