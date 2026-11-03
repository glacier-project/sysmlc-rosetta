"""Test-only observer: log each evaluation of ``label`` to stderr."""

import sys
from collections.abc import Callable

current_tag: Callable[[], tuple[int, int]] | None = None


def mark(label: str) -> int:
    """Print one evaluation of ``label``; return zero."""
    if current_tag is not None:
        ms, microstep = current_tag()
        print(f"TAG: {label} {ms} {microstep}", file=sys.stderr, flush=True)
    print(f"MARK: {label}", file=sys.stderr, flush=True)
    return 0
