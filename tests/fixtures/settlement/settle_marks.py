"""Test-only observers: log marks with their tag; guards read past marks."""

import sys
from collections.abc import Callable

current_tag: Callable[[], tuple[int, int]] | None = None
labels: set[str] = set()


def _log(text: str) -> None:
    tag = "" if current_tag is None else " {} {}".format(*current_tag())
    print(f"MARK: {text}{tag}", file=sys.stderr, flush=True)


def mark(label: str) -> int:
    """Record and log ``label``; return zero."""
    labels.add(label)
    _log(label)
    return 0


def ok(label: str) -> bool:
    """Record and log an always-true guard evaluation."""
    mark(label)
    return True


def seen(label: str) -> bool:
    """Log a guard that holds once ``label`` was marked (or is empty)."""
    result = label == "" or label in labels
    _log(f"guard {label or '-'} {result}")
    return result


def note(label: str, value: int) -> int:
    """Log ``label`` with a payload value; return zero."""
    _log(f"{label} {value}")
    return 0
