"""Include and exclude patterns of a job.

One matcher for both kinds of source. rclone has filters of its own and could have been
handed `--exclude`, but then a pattern would mean one thing on a remote and another on a
local folder, and the difference would only show up as files silently missing from a
backup. The patterns are translated to regular expressions here, once when the job starts,
and applied to the relative path of every file whichever the source is.

The rules, which are the ones the interface documents:

  *   matches inside one path segment              *.tmp
  **  matches across segments                      cache/**
  ?   matches one character                        file?.log
  a pattern without a slash matches the file name at any depth   .DS_Store
  a pattern with a slash matches the whole relative path         Movies/*/sample.mkv
  a pattern ending with a slash matches everything under it      node_modules/

Matching ignores case, because `*.mkv` not catching `FILM.MKV` is never what anybody
meant. Exclude wins over include, and an empty include means everything.
"""

from __future__ import annotations

import re

MAX_PATTERNS = 200


def _translate(pattern: str) -> re.Pattern[str]:
    pattern = pattern.strip().replace("\\", "/")
    # A folder pattern is shorthand for everything under it.
    if pattern.endswith("/"):
        pattern += "**"
    # Without a slash the pattern is about the name, wherever the file sits.
    if "/" not in pattern.rstrip("/"):
        pattern = f"**/{pattern}"

    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            # Zero or more folders, so **/x.txt also matches x.txt at the root.
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1

    return re.compile(f"^{''.join(out)}$", re.IGNORECASE)


def parse_patterns(text: str) -> list[str]:
    """One pattern per line, blank lines and comments dropped."""
    patterns = []
    for line in (text or "").splitlines():
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("#"):
            patterns.append(cleaned)
    return patterns[:MAX_PATTERNS]


class FileFilter:
    def __init__(self, include: str = "", exclude: str = "", max_size: int = 0) -> None:
        self.include_source = parse_patterns(include)
        self.exclude_source = parse_patterns(exclude)
        self._include = [_translate(item) for item in self.include_source]
        self._exclude = [_translate(item) for item in self.exclude_source]
        self.max_size = max(0, max_size)
        self.skipped = 0

    @property
    def active(self) -> bool:
        return bool(self._include or self._exclude or self.max_size)

    def allows(self, rel_path: str, size: int) -> bool:
        if self.max_size and size > self.max_size:
            self.skipped += 1
            return False
        if any(rule.match(rel_path) for rule in self._exclude):
            self.skipped += 1
            return False
        if self._include and not any(rule.match(rel_path) for rule in self._include):
            self.skipped += 1
            return False
        return True

    def describe(self) -> str:
        parts = []
        if self.include_source:
            parts.append(f"{len(self.include_source)} include")
        if self.exclude_source:
            parts.append(f"{len(self.exclude_source)} exclude")
        if self.max_size:
            parts.append(f"over {self.max_size} bytes")
        return ", ".join(parts)


def build_filter(job) -> FileFilter:
    return FileFilter(
        include=getattr(job, "include_globs", "") or "",
        exclude=getattr(job, "exclude_globs", "") or "",
        max_size=getattr(job, "max_file_size", 0) or 0,
    )
