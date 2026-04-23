from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rcc.ignore import GitignoreMatcher


@dataclass(frozen=True)
class TransferPlan:
    absolute_path: Path
    relative_path: str


def build_matcher(exclude_from: Path, extra_excludes: list[str]) -> GitignoreMatcher:
    lines: list[str] = []
    if exclude_from.exists():
        lines.extend(exclude_from.read_text().splitlines())
    lines.extend(extra_excludes)
    return GitignoreMatcher(lines)


def plan_push_transfers(
    source: Path, *, exclude_from: Path, extra_excludes: list[str]
) -> list[TransferPlan]:
    matcher = build_matcher(exclude_from, extra_excludes)
    plans: list[TransferPlan] = []
    for path in source.rglob("*"):
        rel = path.relative_to(source).as_posix()
        if path.is_dir():
            if matcher.match(rel, is_dir=True):
                continue
            continue
        if matcher.match(rel, is_dir=False):
            continue
        plans.append(TransferPlan(absolute_path=path, relative_path=rel))
    return plans


def plan_pull_transfers(
    sftp, remote_root: str, *, exclude_from: Path, extra_excludes: list[str]
) -> list[TransferPlan]:
    import stat

    matcher = build_matcher(exclude_from, extra_excludes)
    plans: list[TransferPlan] = []

    def walk(prefix: str) -> None:
        base = f"{remote_root.rstrip('/')}/{prefix}" if prefix else remote_root.rstrip("/")
        for entry in sftp.listdir_attr(base):
            rel = f"{prefix}/{entry.filename}" if prefix else entry.filename
            is_dir = stat.S_ISDIR(entry.st_mode)
            if matcher.match(rel, is_dir=is_dir):
                continue
            if is_dir:
                walk(rel)
            else:
                plans.append(
                    TransferPlan(
                        absolute_path=Path(f"{remote_root.rstrip('/')}/{rel}"),
                        relative_path=rel,
                    )
                )

    walk("")
    return plans
