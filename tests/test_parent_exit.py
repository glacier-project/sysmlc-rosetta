"""A selected parent departure exits its active descendants first."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program
from tests.test_lf_harness import run_part
from tests.test_settlement import (
    FIXTURES,
    MARKS,
    _labels,
    _marks,
    _run,
    _states,
    _stop,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.lf
def test_nested_descendants_exit_before_the_parent(tmp_path: Path) -> None:
    marks = _run(tmp_path, "NestedExit", _stop(100, 3, ""))
    assert _labels(marks) == [
        "guard - True",
        "leaf.exit",
        "mid.exit",
        "outer.exit",
        "stop.effect 3",
        "stopped.entry",
    ]
    assert _states(marks)[-1] == "stopped"


@pytest.mark.lf
def test_false_parent_guard_leaves_descendants_active(tmp_path: Path) -> None:
    marks = _run(tmp_path, "NestedExit", _stop(100, 3, "never"))
    assert _labels(marks) == ["guard never False"]
    assert _states(marks) == ["outer.mid.leaf"]


@pytest.mark.lf
@pytest.mark.parametrize(
    ("ms", "sibling"),
    [
        (200, []),
        (300, ["b.exit", "ping.accepted"]),
    ],
)
def test_regions_drain_in_order_skipping_finalized(
    tmp_path: Path, ms: int, sibling: list[str]
) -> None:
    marks = _run(tmp_path, "ParallelExit", _stop(ms, 5, ""))
    forced_b = [] if sibling else ["b.exit"]
    assert _labels(marks) == [
        "r1.exit",
        *sibling,
        "guard - True",
        *forced_b,
        "r2.exit",
        "d.exit",
        "c.exit",
        "r3.exit",
        "working.exit",
        "stop.effect 5",
        "stopped.entry",
    ]
    assert "working.r2.b" in _states(marks)
    assert _states(marks)[-1] == "stopped"


@pytest.mark.lf
def test_drained_escape_keeps_effect_before_enclosing_exits(
    tmp_path: Path,
) -> None:
    marks = _run(tmp_path, "DeepEscape", _stop(100, 1, ""))
    assert _labels(marks) == [
        "leaf.exit",
        "p.exit",
        "p.effect",
        "s.exit",
        "g.exit",
        "outside.entry",
    ]
    assert _states(marks)[-1] == "outside"


@pytest.mark.lf
def test_forced_exit_output_reaches_the_peer_once(tmp_path: Path) -> None:
    logs, returncode = run_part(
        tmp_path,
        FIXTURES,
        "Settlement::peers",
        timeout="300 msec",
        python_file=MARKS,
    )
    assert returncode == 0, logs
    marks = _marks(logs)
    sender = [m for m in marks if m.startswith("sender.")]
    assert sender == [
        "sender.leaf.exit",
        "sender.outer.exit",
        "sender.effect",
        "sender.stopped.entry",
    ]
    assert marks.count("waiter.got") == 1
    assert marks.count("waiter.payload 7") == 1
    assert marks.index("waiter.got") > marks.index("sender.leaf.exit")


@pytest.mark.lf
@pytest.mark.parametrize(
    ("need", "expected", "final"),
    [
        ("", ["guard - True", "r1.effect 2"], "working.r1.a2"),
        (
            "never",
            ["guard never False", "r2.guard", "r2.effect 2"],
            "working.r2.b2",
        ),
    ],
)
def test_shared_signal_is_accepted_by_one_region(
    tmp_path: Path, need: str, expected: list[str], final: str
) -> None:
    marks = _run(tmp_path, "SharedStop", _stop(100, 2, need))
    assert _labels(marks) == expected
    assert final in _states(marks)


@pytest.mark.lf
@pytest.mark.parametrize(
    ("ident", "need", "outcome", "final"),
    [
        (4, "running.entry", ["r1.effect 4"], "working.r1.keep.q2"),
        (4, "never", ["r2.guard", "r2.effect 4"], "working.r2.b2"),
        (5, "never", ["keep.effect 5"], "working.r1.keep2"),
        (
            7,
            "never",
            ["r2.guard", "fallback.effect 7", "stopped.entry"],
            "stopped",
        ),
    ],
)
def test_held_shared_signal_waits_for_the_earlier_region(
    tmp_path: Path, ident: int, need: str, outcome: list[str], final: str
) -> None:
    marks = _run(tmp_path, "SharedHeld", _stop(200, ident, need))
    assert _labels(marks) == [
        "running.entry",
        f"guard {need} {need != 'never'}",
        *outcome,
    ]
    assert final in _states(marks)


@pytest.mark.lf
@pytest.mark.parametrize(
    ("need", "expected", "final"),
    [
        ("", ["guard - True", "r1.effect 2"], "r1.a2"),
        ("never", ["guard never False", "r2.guard", "r2.effect 2"], "r2.b2"),
    ],
)
def test_parallel_root_accepts_a_shared_signal_once(
    tmp_path: Path, need: str, expected: list[str], final: str
) -> None:
    marks = _run(tmp_path, "SharedRoot", _stop(100, 2, need))
    assert _labels(marks) == expected
    assert final in _states(marks)


@pytest.mark.lf
def test_released_inner_holder_keeps_enclosing_hold(tmp_path: Path) -> None:
    marks = _run(tmp_path, "SharedHeldEscape", _stop(200, 6, ""))
    assert _labels(marks) == ["caught.entry", "keep.effect 6"]
    assert "working.r1.keep2" in _states(marks)


@pytest.mark.parametrize(
    ("name", "generated"),
    [
        ("OriginClash", "Stop_origin"),
        ("WaitClash", "_working_r2_Stop_wait"),
        ("DueClash", "_t_idle_due"),
    ],
)
def test_shared_signal_name_clash_is_rejected(
    name: str, generated: str
) -> None:
    with pytest.raises(UnsupportedConstructError, match=generated):
        build_program(load_model(FIXTURES), f"Settlement::{name}")


def test_late_exit_port_name_clash_is_rejected() -> None:
    with pytest.raises(UnsupportedConstructError, match="exit_0_done"):
        build_program(load_model(FIXTURES), "Settlement::LatePortClash")
