"""A new source activation starts from fresh lifecycle state."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.test_settlement import _labels, _run, _states, _stop, _times

if TYPE_CHECKING:
    from pathlib import Path


def _send(ms: int, port: str) -> str:
    return (
        f"  timer send_{port}_{ms}({ms} msec)\n"
        f"  reaction(send_{port}_{ms}) -> m.{port} {{=\n"
        f"    m.{port}.set(None)\n"
        "  =}\n"
    )


def _at(marks: list[str], label: str) -> list[int]:
    return sorted(
        _times([mark]).pop()
        for mark in marks
        if mark.rsplit(" ", 2)[0] == label
    )


@pytest.mark.lf
def test_self_transition_restarts_the_child_timer(tmp_path: Path) -> None:
    marks = _run(tmp_path, "RestartTimer", _send(100, "Go"), timeout="600 msec")
    assert _labels(marks) == ["restart.effect", "timer.effect"]
    assert _at(marks, "timer.effect") == [400]


@pytest.mark.lf
def test_reentered_regions_finalize_afresh(tmp_path: Path) -> None:
    drivers = _send(100, "Stop") + _send(200, "Go")
    marks = _run(tmp_path, "ParallelReentry", drivers, timeout="600 msec")
    assert _at(marks, "ra.exit") == [50, 250]
    assert _at(marks, "rb.exit") == [100, 500]


@pytest.mark.lf
@pytest.mark.parametrize(
    ("ms", "accepted"),
    [
        (200, []),
        (250, ["c.effect 4"]),
    ],
)
def test_delayed_shared_signal_needs_an_active_source(
    tmp_path: Path, ms: int, accepted: list[str]
) -> None:
    marks = _run(tmp_path, "SharedLifetime", _stop(ms, 4, "never"))
    assert "c.entry" in _labels(marks)
    assert [m for m in _labels(marks) if m.startswith("c.effect")] == accepted
    assert ("working.r2.c2" in _states(marks)) == bool(accepted)


@pytest.mark.lf
def test_reentered_parallel_starts_with_an_empty_gate(tmp_path: Path) -> None:
    drivers = _stop(200, 1, "") + _send(300, "Go") + _stop(400, 2, "")
    marks = _run(tmp_path, "SharedReentry", drivers, timeout="500 msec")
    assert [m for m in _labels(marks) if "effect" in m] == ["r1.effect 2"]
    assert "working.r1.q2" in _states(marks)


@pytest.mark.lf
@pytest.mark.parametrize(
    ("port", "fires"),
    [
        ("Go", [500]),
        ("Ping", []),
    ],
)
def test_self_transition_resamples_the_at_instant(
    tmp_path: Path, port: str, fires: list[int]
) -> None:
    marks = _run(tmp_path, "RetimeAt", _send(100, port), timeout="700 msec")
    assert _at(marks, "at.effect") == fires


@pytest.mark.lf
def test_same_deadline_callbacks_evaluate_the_guard_once(
    tmp_path: Path,
) -> None:
    marks = _run(tmp_path, "SameDue", _send(100, "Go"), timeout="500 msec")
    assert _at(marks, "guard never False") == [300]
