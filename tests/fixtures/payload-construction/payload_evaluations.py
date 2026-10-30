"""Count runtime evaluations of send arguments."""

calls = 0


def sample(value: int) -> int:
    """Count one evaluation; fold the count into the result."""
    global calls
    calls += 1
    return value * 10 + calls


def evaluations() -> int:
    """Return how many times ``sample`` has been evaluated."""
    return calls
