import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest
import syside
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.foreign_artifact.base import ForeignArtifact
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import ENTRY_ORDER_PORT, build_program
from sysmlc_rosetta.program import LfProgram, Mode, Reaction, Reactor
from sysmlc_rosetta.serialize import to_lf
from tests.test_lf_harness import compile_harness

FIXTURES = Path(__file__).parent / "fixtures" / "entry-order"
MARKS = FIXTURES / "entry_marks.py"

# Entry-before-middle is required by StatePerformances/States; completing
# finite do work first is SysMLC policy. Parallel checks assert causal edges.
NESTED = ["outer.entry.first", "outer.entry.last", "outer.do", "mid", "leaf"]
BOTH = ["both", "both.do", "r1", "r2", "r1.a", "r2.b"]
BOTH_EDGES = [
    ("both", "both.do"),
    ("both.do", "r1"),
    ("both.do", "r2"),
    ("r1", "r1.a"),
    ("r2", "r2.b"),
]
TAG = (
    "  reaction(m.current_state) {=\n"
    '    print(f"TAG: {lf.time.logical_elapsed() // 1000000} '
    '{lf.tag().microstep} {m.current_state.value}")\n'
    "  =}\n"
)
GO_AT_STARTUP = "  reaction(startup) -> m.Go {=\n    m.Go.set(True)\n  =}\n"


@pytest.fixture(scope="module")
def entry_model() -> syside.Model:
    return load_model(FIXTURES)


def _run(
    tmp_path: Path, name: str, timeout: str, drivers: str = TAG
) -> tuple[list[str], list[str]]:
    """Run one machine; return its entry marks and (ms, microstep) tags."""
    binary = compile_harness(
        tmp_path,
        FIXTURES,
        f"EntryOrder::{name}",
        timeout=timeout,
        drivers=drivers,
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
    lines = result.stdout.splitlines()
    marks = [line.removeprefix("MARK: ") for line in lines if "MARK: " in line]
    tags = [line.removeprefix("TAG: ") for line in lines if "TAG: " in line]
    return marks, tags


def _split(marks: list[str], departure: str) -> list[list[str]]:
    chunks: list[list[str]] = [[]]
    for mark in marks:
        if mark == departure:
            chunks.append([])
        else:
            chunks[-1].append(mark)
    return chunks


def _assert_activation(
    chunk: list[str], expected: list[str], edges: list[tuple[str, str]]
) -> None:
    assert Counter(chunk) == Counter(expected), chunk
    for earlier, later in edges:
        assert chunk.index(earlier) < chunk.index(later), (earlier, chunk)


def test_entry_order_source_renders_before_effects() -> None:
    reaction = Reaction(
        ("reset", "startup"), ("current_state",), ("pass",), ("_entry_order",)
    )
    mode = Mode("a", initial=True, reactions=(reaction,))
    reactor = Reactor("R", inputs=("_entry_order",), modes=(mode,))
    text = to_lf(LfProgram(reactors=(reactor,)))
    assert "reaction(reset, startup) _entry_order -> current_state {=" in text


def test_entry_order_is_a_source_never_a_trigger(
    entry_model: syside.Model,
) -> None:
    program = build_program(
        entry_model,
        "EntryOrder::Nested",
        external=[ForeignArtifact(MARKS, "python")],
    )
    *children, root = program.reactors
    assert ENTRY_ORDER_PORT not in root.inputs
    for reactor in children:
        assert ENTRY_ORDER_PORT in reactor.inputs
        for mode in reactor.modes:
            entry, *others = mode.reactions
            assert entry.triggers == ("reset", "startup")
            assert entry.sources == (ENTRY_ORDER_PORT,)
            assert all(not r.sources for r in others)
    for reactor in program.reactors:
        reactions = [
            *reactor.reactions,
            *(r for mode in reactor.modes for r in mode.reactions),
        ]
        assert all(
            ENTRY_ORDER_PORT not in trigger
            for r in reactions
            for trigger in r.triggers
        )
    outer_entry = next(m for m in root.modes if m.name == "outer").reactions[0]
    assert f"c_outer.{ENTRY_ORDER_PORT}" in outer_entry.effects


def test_reserved_entry_order_state_name_is_rejected(
    entry_model: syside.Model,
) -> None:
    with pytest.raises(UnsupportedConstructError, match="_entry_order"):
        build_program(entry_model, "EntryOrder::ReservedState")


@pytest.mark.lf
def test_nested_entries_run_parent_first_on_every_activation(
    tmp_path: Path,
) -> None:
    marks, tags = _run(tmp_path, "Nested", "450 msec")
    expected = ["root", *NESTED, "away", *NESTED, "away", *NESTED]
    assert marks == expected
    assert tags == [
        "0 0 outer.mid.leaf",
        "100 1 away",
        "200 1 outer.mid.leaf",
        "300 1 away",
        "400 1 outer.mid.leaf",
    ]


@pytest.mark.lf
def test_zero_offset_child_timer_keeps_its_tag(tmp_path: Path) -> None:
    marks, tags = _run(tmp_path, "ZeroTimer", "450 msec")
    activation = ["outer", "waiting", "timed"]
    assert marks == [*activation, "away", *activation, "away", *activation]
    assert tags == [
        "0 0 outer.waiting",
        "0 1 outer.timed",
        "100 1 away",
        "200 1 outer.waiting",
        "200 2 outer.timed",
        "300 1 away",
        "400 1 outer.waiting",
        "400 2 outer.timed",
    ]


@pytest.mark.lf
def test_input_at_the_activation_tag_is_still_accepted(
    tmp_path: Path,
) -> None:
    marks, tags = _run(
        tmp_path, "SameTagInput", "100 msec", GO_AT_STARTUP + TAG
    )
    assert marks == ["outer", "waiting", "got"]
    assert tags == ["0 0 outer.waiting", "0 1 outer.got"]


@pytest.mark.lf
def test_nested_parallel_entries_run_before_region_substates(
    tmp_path: Path,
) -> None:
    marks, _tags = _run(tmp_path, "ParallelNested", "450 msec")
    assert marks.count("away") == 2
    activations = _split(marks, "away")
    assert len(activations) == 3
    for chunk in activations:
        _assert_activation(chunk, BOTH, BOTH_EDGES)


@pytest.mark.lf
def test_parallel_root_runs_region_entries_once_before_substates(
    tmp_path: Path,
) -> None:
    marks, _tags = _run(tmp_path, "ParallelRoot", "100 msec")
    _assert_activation(
        marks,
        ["root", "root.do", "r1", "r1.do", "r2", "r1.a", "r2.b"],
        [
            ("root", "root.do"),
            ("root.do", "r1"),
            ("root.do", "r2"),
            ("r1", "r1.do"),
            ("r1.do", "r1.a"),
            ("r2", "r2.b"),
        ],
    )


@pytest.mark.lf
def test_empty_parallel_root_enters_region_substates_once(
    tmp_path: Path, entry_model: syside.Model
) -> None:
    program = build_program(
        entry_model,
        "EntryOrder::ParallelRootEmpty",
        external=[ForeignArtifact(MARKS, "python")],
    )
    assert all(r.triggers != ("startup",) for r in program.reactor.reactions)
    assert all(
        ENTRY_ORDER_PORT not in effect
        for r in program.reactor.reactions
        for effect in r.effects
    )
    marks, tags = _run(tmp_path, "ParallelRootEmpty", "100 msec")
    _assert_activation(marks, ["r1.a", "r2.b"], [])
    assert len(tags) == 1


@pytest.mark.lf
def test_empty_parent_child_entry_runs_once(tmp_path: Path) -> None:
    marks, tags = _run(tmp_path, "EmptyParent", "100 msec")
    assert marks == ["leaf"]
    assert tags == ["0 0 outer.leaf"]
