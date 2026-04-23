from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class _Rule:
    pattern: str
    negate: bool
    dir_only: bool
    root_anchored: bool


class GitignoreMatcher:
    """Supported gitignore subset: directory rules, globs, root anchors, and negation."""

    def __init__(self, patterns: list[str]) -> None:
        self._rules = [rule for rule in map(self._parse, patterns) if rule is not None]

    @classmethod
    def from_file(cls, path: Path) -> GitignoreMatcher:
        return cls(path.read_text().splitlines())

    @staticmethod
    def _parse(raw: str) -> _Rule | None:
        line = raw.strip()
        if not line or line.startswith("#"):
            return None
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        root_anchored = line.startswith("/")
        if root_anchored:
            line = line[1:]
        dir_only = line.endswith("/")
        if dir_only:
            line = line[:-1]
        if not line:
            return None
        return _Rule(pattern=line, negate=negate, dir_only=dir_only, root_anchored=root_anchored)

    def match(self, path: Path | str, *, is_dir: bool) -> bool:
        clean = str(path).replace("\\", "/").strip("/")
        if not clean:
            return False
        parts = PurePosixPath(clean).parts
        matched = False
        for rule in self._rules:
            if self._matches_rule(parts, is_dir=is_dir, rule=rule):
                matched = not rule.negate
        return matched

    @staticmethod
    def _matches_rule(parts: tuple[str, ...], *, is_dir: bool, rule: _Rule) -> bool:
        joined = "/".join(parts)
        if rule.dir_only:
            if is_dir and _path_or_basename_matches(parts, joined, rule):
                return True
            return _has_dir_ancestor_matching(parts, rule)
        return _path_or_basename_matches(parts, joined, rule)


def _path_or_basename_matches(parts: tuple[str, ...], joined: str, rule: _Rule) -> bool:
    if rule.root_anchored:
        return fnmatch.fnmatchcase(joined, rule.pattern)
    if "/" in rule.pattern:
        return fnmatch.fnmatchcase(joined, rule.pattern)
    return any(fnmatch.fnmatchcase(part, rule.pattern) for part in parts)


def _has_dir_ancestor_matching(parts: tuple[str, ...], rule: _Rule) -> bool:
    candidates = parts if not rule.root_anchored else parts[:1]
    return any(fnmatch.fnmatchcase(part, rule.pattern) for part in candidates)
