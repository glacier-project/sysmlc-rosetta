import os
import subprocess
from pathlib import Path

import pytest
import syside
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.foreign_artifact.base import ForeignArtifact
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import DONE_MODE, build_program
from tests.test_lf_harness import compile_harness, run_part

FIXTURES = Path(__file__).parent / "fixtures" / "root-exit"
MARKS = FIXTURES / "root_marks.py"
EXTERNAL = [ForeignArtifact(MARKS, "python")]
# Harness observers share the stderr stream with the model's marks, so the
# relative order and (ms, microstep) tags of both are visible. The keepalive
# timer makes an unfinished run end at its timeout, not on an empty queue.
OBSERVE = (
    "  reaction(startup) {=\n"
    "    import root_marks\n"
    "    root_marks.current_tag = lambda: (\n"
    "        lf.time.logical_elapsed() // 1000000, lf.tag().microstep)\n"
    "  =}\n"
    "  timer keepalive(0, 50 msec)\n"
    "  reaction(keepalive) {=\n"
    "    pass\n"
    "  =}\n"
    "  reaction(m.current_state) {=\n"
    "    import root_marks\n"
    '    root_marks.mark(f"state {m.current_state.value} '
    '{lf.time.logical_elapsed() // 1000000} {lf.tag().microstep}")\n'
    "  =}\n"
    "  reaction(shutdown) {=\n"
    "    import root_marks\n"
    '    root_marks.mark(f"shutdown '
    '{lf.time.logical_elapsed() // 1000000} {lf.tag().microstep}")\n'
    "  =}\n"
)


@pytest.fixture(scope="module")
def model() -> syside.Model:
    return load_model(FIXTURES)


def _marks(stderr: str) -> list[str]:
    return [
        line.removeprefix("MARK: ")
        for line in stderr.splitlines()
        if line.startswith("MARK: ")
    ]


def _run(
    tmp_path: Path, name: str, timeout: str, *, tagged: bool = False
) -> list[str]:
    binary = compile_harness(
        tmp_path,
        FIXTURES,
        f"RootExit::{name}",
        timeout=timeout,
        drivers=OBSERVE,
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
    if tagged:
        return [
            line.removeprefix("TAG: ")
            for line in result.stderr.splitlines()
            if line.startswith("TAG: ")
        ]
    return _marks(result.stderr)


def test_root_exit_is_never_a_shutdown_reaction(model: syside.Model) -> None:
    program = build_program(model, "RootExit::Finishing", external=EXTERNAL)
    for reactor in program.reactors:
        reactions = [
            *reactor.reactions,
            *(r for mode in reactor.modes for r in mode.reactions),
        ]
        assert all("shutdown" not in r.triggers for r in reactions)
    root = program.reactor
    done = next(mode for mode in root.modes if mode.name == DONE_MODE)
    (entry,) = done.reactions
    # The exit's self-send schedules a reactor-level action, which LF only
    # passes into reactions that declare it.
    assert "Bye_act" in entry.effects
    assert "Bye_act" in {action.name for action in root.actions}


@pytest.mark.parametrize(
    "name", ["BadExitNoDone", "BadExitParallelNoDone", "BadRegionExitNoDone"]
)
def test_unsupported_exit_without_completion_is_rejected(
    model: syside.Model, name: str
) -> None:
    with pytest.raises(UnsupportedConstructError, match="unbacked"):
        build_program(model, f"RootExit::{name}", external=EXTERNAL)


@pytest.mark.lf
def test_waiting_root_does_not_exit_at_timeout(tmp_path: Path) -> None:
    marks = _run(tmp_path, "Waiting", "200 msec")
    assert marks == ["state idle 0 0", "shutdown 200 0"]


@pytest.mark.lf
def test_done_runs_root_exit_once_before_shutdown(tmp_path: Path) -> None:
    marks = _run(tmp_path, "Finishing", "1 sec")
    assert marks == [
        "state idle 0 0",
        "idle.exit",
        "effect",
        "root.exit",
        "state done 100 1",
        "shutdown 100 2",
    ]


@pytest.mark.lf
def test_partial_parallel_join_does_not_exit_root(tmp_path: Path) -> None:
    marks = _run(tmp_path, "ParallelStaggered", "150 msec")
    assert "state r2.done 100 1" in marks
    # A ready region does not wait for an earlier-declared sibling.
    assert "r2.exit" in marks
    assert "r1.exit" not in marks
    assert "root.exit" not in marks
    assert marks[-1] == "shutdown 150 0"


@pytest.mark.lf
@pytest.mark.parametrize(
    ("name", "exits"),
    [
        ("ParallelStaggered", ["r2.exit 100", "r1.exit 200", "root.exit 200"]),
        (
            "ParallelSimultaneous",
            ["r2.exit 100", "r1.exit 100", "root.exit 100"],
        ),
    ],
)
def test_ready_parallel_join_exits_root_once(
    tmp_path: Path, name: str, exits: list[str]
) -> None:
    marks = _run(tmp_path, name, "1 sec", tagged=True)
    exit_marks = [m.split() for m in marks if ".exit " in m]
    assert [f"{label} {ms}" for label, ms, _ in exit_marks] == exits
    exit_tags = [(int(ms), int(step)) for _, ms, step in exit_marks]
    assert marks[-1].startswith("shutdown ")
    shutdown_tag = tuple(map(int, marks[-1].split()[-2:]))
    assert all(tag < shutdown_tag for tag in exit_tags)
    if name == "ParallelSimultaneous":
        assert len(set(exit_tags)) == 1


@pytest.mark.lf
def test_nested_region_exits_precede_parent_guard(tmp_path: Path) -> None:
    marks = _run(tmp_path, "NestedParallelGuard", "300 msec", tagged=True)
    labels = [m.split()[0] for m in marks]
    exits = [m.split() for m in marks if ".exit " in m]
    # Each region exits when ready, not at the later join.
    assert [f"{label} {ms}" for label, ms, _ in exits] == [
        "motor.exit 100",
        "light.exit 200",
    ]
    # motor.exit set allow to false before the parent guard was evaluated.
    assert "effect" not in labels
    assert "dest.entry" not in labels
    states = [m.split()[1] for m in marks if m.startswith("state ")]
    assert states[-1].startswith("working.")
    assert marks[-1].startswith("shutdown 300 ")


@pytest.mark.lf
def test_peer_completion_does_not_exit_unfinished_peer(
    tmp_path: Path,
) -> None:
    logs, returncode = run_part(
        tmp_path,
        FIXTURES,
        "RootExit::peers",
        timeout="1 sec",
        python_file=MARKS,
    )
    assert returncode == 0, logs
    marks = _marks(logs)
    # The finisher's region-exit send reaches the waiter before the global
    # stop; the waiter is still running, so its root exit never executes.
    assert marks == [
        "finisher.work.exit",
        "finisher.exit",
        "waiter.waiting.exit",
    ]
