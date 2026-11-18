"""Choose which operations become MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from .spec import Operation

READ_ONLY_METHODS = ("GET",)


@dataclass
class Selection:
    include_tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    include_paths: list[str] = field(default_factory=list)  # glob patterns, e.g. /pet/*
    exclude_paths: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=lambda: list(READ_ONLY_METHODS))
    include_deprecated: bool = False
    max_tools: int | None = 40


@dataclass
class SelectionResult:
    selected: list[Operation]
    dropped_by_cap: list[Operation]
    candidates: int  # operations that passed the filters, before the cap


def _lower(items: list[str]) -> set[str]:
    return {i.lower() for i in items}


def select_operations(operations: list[Operation], sel: Selection) -> SelectionResult:
    methods = {m.upper() for m in sel.methods}
    inc_tags, exc_tags = _lower(sel.include_tags), _lower(sel.exclude_tags)
    passed: list[Operation] = []
    for op in operations:
        if op.method not in methods:
            continue
        if op.deprecated and not sel.include_deprecated:
            continue
        tags = _lower(op.tags)
        if inc_tags and not (tags & inc_tags):
            continue
        if exc_tags and (tags & exc_tags):
            continue
        if sel.include_paths and not any(fnmatchcase(op.path, p) for p in sel.include_paths):
            continue
        if sel.exclude_paths and any(fnmatchcase(op.path, p) for p in sel.exclude_paths):
            continue
        passed.append(op)
    cap = sel.max_tools
    if cap is not None and cap >= 0 and len(passed) > cap:
        return SelectionResult(passed[:cap], passed[cap:], len(passed))
    return SelectionResult(passed, [], len(passed))
