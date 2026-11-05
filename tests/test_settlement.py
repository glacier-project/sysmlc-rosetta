"""Ancestor triggers wait for the selected child transaction to settle."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.test_lf_harness import compile_harness

FIXTURES = Path(__file__).parent / "fixtures" / "settlement"
MARKS = FIXTURES / "settle_marks.py"
OBSERVE = (
    "  reaction(startup) {=\n"
    "    import settle_marks\n"
    "    settle_marks.current_tag = lambda: (\n"
    "        lf.time.logical_elapsed() // 1000000, lf.tag().microstep)\n"
    "  =}\n"
    "  reaction(m.current_state) {=\n"
    "    import settle_marks\n"
    '    settle_marks._log(f"state {m.current_state.value}")\n'
    "  =}\n"
)


def _stop(ms: int, ident: int, need: str) -> str:
    return (
        f"  timer stop_{ident}({ms} msec)\n"
        f"  reaction(stop_{ident}) -> m.Stop {{=\n"
        "    import types\n"
        f"    m.Stop.set(types.SimpleNamespace(id={ident}, "
        f"need={json.dumps(need)}))\n"
        "  =}\n"
    )


def _then(ident: int, port: str, value: str) -> str:
    """Send ``value`` on ``port`` one microstep after Stop ``ident``."""
    return (
        f"  logical action then_{ident}\n"
        f"  reaction(stop_{ident}) -> then_{ident} {{=\n"
        f"    then_{ident}.schedule(0)\n"
        "  =}\n"
        f"  reaction(then_{ident}) -> m.{port} {{=\n"
        "    import types\n"
        f"    m.{port}.set({value})\n"
        "  =}\n"
    )


def _run(
    tmp_path: Path, name: str, drivers: str = "", timeout: str = "400 msec"
) -> list[str]:
    """Run one machine; return its marks as ``label ms microstep``."""
    result = _execute(tmp_path, name, drivers, timeout)
    assert result.returncode == 0, result.stderr
    return _marks(result.stderr)


def _marks(stderr: str) -> list[str]:
    return [
        line.removeprefix("MARK: ")
        for line in stderr.splitlines()
        if line.startswith("MARK: ")
    ]


def _execute(
    tmp_path: Path, name: str, drivers: str, timeout: str = "400 msec"
) -> subprocess.CompletedProcess[str]:
    binary = compile_harness(
        tmp_path,
        FIXTURES,
        f"Settlement::{name}",
        timeout=timeout,
        drivers=drivers + OBSERVE,
        python_file=MARKS,
    )
    return subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(tmp_path / "src")},
    )


def _labels(marks: list[str]) -> list[str]:
    """Model marks without tags; harness state lines are excluded.

    The harness prints a state after the entry marks of that state, so
    states are asserted separately from the phase order.
    """
    return [
        mark.rsplit(" ", 2)[0]
        for mark in marks
        if not mark.startswith("state ")
    ]


def _states(marks: list[str]) -> list[str]:
    return [
        mark.rsplit(" ", 2)[0].removeprefix("state ")
        for mark in marks
        if mark.startswith("state ")
    ]


def _times(marks: list[str]) -> set[int]:
    return {int(mark.rsplit(" ", 2)[1]) for mark in marks}


@pytest.mark.lf
def test_tied_stop_waits_for_eventless_closure(tmp_path: Path) -> None:
    marks = _run(tmp_path, "TimerStop", _stop(200, 7, "ready.entry"))
    assert _labels(marks) == [
        "warmup.exit",
        "timer.effect",
        "running.entry",
        "running.auto",
        "ready.entry",
        "guard ready.entry True",
        "ready.exit",
        "working.exit",
        "stop.effect 7",
        "stopped.entry",
    ]
    assert _states(marks) == [
        "working.warmup",
        "working.running",
        "working.ready",
        "stopped",
    ]
    assert _times(marks[1:]) == {200}


@pytest.mark.lf
def test_earlier_stop_prevents_the_child_timer(tmp_path: Path) -> None:
    marks = _run(tmp_path, "TimerStop", _stop(150, 3, ""))
    assert _labels(marks) == [
        "guard - True",
        "warmup.exit",
        "working.exit",
        "stop.effect 3",
        "stopped.entry",
    ]
    assert _states(marks) == ["working.warmup", "stopped"]
    assert _times(marks[1:]) == {150}


@pytest.mark.lf
def test_false_parent_retires_the_retained_stop(tmp_path: Path) -> None:
    marks = _run(
        tmp_path,
        "TimerStop",
        _stop(200, 1, "never") + _stop(300, 2, ""),
    )
    assert _labels(marks) == [
        "warmup.exit",
        "timer.effect",
        "running.entry",
        "running.auto",
        "ready.entry",
        "guard never False",
        "guard - True",
        "ready.exit",
        "working.exit",
        "stop.effect 2",
        "stopped.entry",
    ]
    refusal = next(m for m in marks if m.startswith("guard never"))
    assert _times([refusal]) == {200}
    assert _times([m for m in marks if m.startswith("stop.effect")]) == {300}


@pytest.mark.lf
@pytest.mark.parametrize(
    ("name", "effect"),
    [("TimerTie", "timeout.effect"), ("AtTie", "at.effect")],
)
def test_parent_time_trigger_waits_for_settlement(
    tmp_path: Path, name: str, effect: str
) -> None:
    marks = _run(tmp_path, name)
    assert _labels(marks) == [
        "running.entry",
        "running.auto",
        "ready.entry",
        "guard ready.entry True",
        "ready.exit",
        effect,
        "stopped.entry",
    ]
    assert _states(marks)[-1] == "stopped"
    assert _times(marks[1:]) == {200}


@pytest.mark.lf
def test_initial_change_waits_for_initial_closure(tmp_path: Path) -> None:
    marks = _run(tmp_path, "WhenAtEntry")
    assert _labels(marks) == [
        "warm.auto",
        "running.entry",
        "running.auto",
        "ready.entry",
        "guard ready.entry True",
        "ready.exit",
        "when.effect",
        "stopped.entry",
    ]
    assert _states(marks)[-1] == "stopped"
    assert _times(marks) == {0}


@pytest.mark.lf
def test_region_finalization_precedes_retained_stop(tmp_path: Path) -> None:
    marks = _run(tmp_path, "RegionFinalize", _stop(200, 5, "r1.exit"))
    assert _labels(marks) == [
        "r1.exit",
        "guard r1.exit True",
        "stop.effect 5",
        "stopped.entry",
    ]
    assert "working.r1.done" in _states(marks)
    assert _states(marks)[-1] == "stopped"
    assert _times(marks[2:]) == {200}


@pytest.mark.lf
def test_second_retained_occurrence_is_diagnosed(tmp_path: Path) -> None:
    drivers = _stop(200, 1, "never") + _then(
        1, "Stop", 'types.SimpleNamespace(id=2, need="ready.entry")'
    )
    result = _execute(tmp_path, "NestedStop", drivers)
    assert result.returncode != 0
    assert "overlapping retained events are not supported" in result.stderr
    labels = _labels(_marks(result.stderr))
    assert "caught.entry" not in labels
    assert not any(label.startswith("outer.effect") for label in labels)


@pytest.mark.lf
@pytest.mark.parametrize(
    ("name", "need", "accepted"),
    [
        ("RegionFinalize", "r1.exit", "working.r2.c"),
        ("EscapeOvertake", "", "escaped"),
    ],
)
def test_later_event_overtaking_a_retained_stop_is_diagnosed(
    tmp_path: Path, name: str, need: str, accepted: str
) -> None:
    drivers = _stop(200, 4, need) + _then(4, "Go", "None")
    result = _execute(tmp_path, name, drivers)
    assert result.returncode != 0
    assert "reordering overlapping events is not supported" in result.stderr
    marks = _marks(result.stderr)
    assert not any(label.startswith("stop.effect") for label in _labels(marks))
    assert accepted not in _states(marks)


@pytest.mark.lf
@pytest.mark.parametrize(
    ("name", "reverse_order"),
    [
        ("EntryEnabled", False),
        ("EntryDisabled", False),
        ("FiniteSelfLoop", False),
        ("NestedLeaf", False),
        pytest.param("EntryEnabled", True, id="enabled-reversed"),
    ],
)
def test_leaf_entry_closure_excludes_triggered_departure(
    tmp_path: Path, name: str, reverse_order: bool
) -> None:
    expectations = {
        "EntryEnabled": (
            [
                "source.entry",
                "source.exit",
                "eventless.effect",
                "automatic.entry",
            ],
            ["source", "automatic"],
        ),
        "EntryDisabled": (
            ["source.entry", "source.exit", "timer.effect", "timed.entry"],
            ["source", "timed"],
        ),
        "FiniteSelfLoop": (
            [
                "source.entry 0",
                "source.exit",
                "source.entry 1",
                "source.exit",
                "source.entry 2",
                "source.exit",
                "timer.effect",
                "timed.entry 2",
            ],
            ["source", "source", "source", "timed"],
        ),
        "NestedLeaf": (
            [
                "source.entry",
                "source.exit",
                "eventless.effect",
                "automatic.entry",
                "automatic.exit",
                "outer.exit",
                "parent.effect",
                "outside.entry",
            ],
            ["outer.source", "outer.automatic", "outside"],
        ),
    }
    expected, states = expectations[name]
    model_dir = FIXTURES
    if reverse_order:
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        source = (FIXTURES / "settlement.sysml").read_text()
        eventless = (
            "        transition first source if true\n"
            '            do assign n := mark("eventless.effect") then automatic;\n'
        )
        timer = (
            "        transition first source accept after 0 [s]\n"
            '            do assign n := mark("timer.effect") then timed;\n'
        )
        assert source.count(eventless + timer) == 1
        source = source.replace(eventless + timer, timer + eventless, 1)
        (model_dir / "settlement.sysml").write_text(source)
    binary = compile_harness(
        tmp_path,
        model_dir,
        f"Settlement::{name}",
        timeout="1 msec",
        python_file=MARKS,
    )
    result = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(tmp_path / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert _marks(result.stderr) == expected
    assert [
        line.removeprefix("STATE: ")
        for line in result.stdout.splitlines()
        if line.startswith("STATE: ")
    ] == states
