"""Test-only observer: log each evaluation of a send payload argument."""

marks = 0


def mark(label: str) -> int:
    """Print one evaluation of ``label``; return the running mark count."""
    global marks
    marks += 1
    print(f"MARK: {label}", flush=True)
    return marks
