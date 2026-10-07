"""Compile generated LF programs with lfc and execute them.

Each test renders the machine with the rosetta backend, writes a harness
that imports the machine reactor (the machine file's own ``main reactor``
is ignored on import -- verified against lfc 0.11), drives scripted
inputs, and asserts the sequence of ``current_state`` announcements
printed by the harness. ``fast: true`` makes logical time run
unthrottled, so ``after`` delays don't cost wall-clock time.

Each test compiles with lfc (seconds per test); run with ``-m "not lf"``
for a fast local loop when lfc is installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from sysmlc.sysml.foreign_artifact.base import ForeignArtifact
from sysmlc.sysml.loading import load_model
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.parts import build_part_program
from sysmlc_rosetta.serialize import to_lf
from tests.conftest import FIXTURES_DIR
from tests.test_lf_harness import (
    run_machine,
    run_part,
    run_rig,
)

if TYPE_CHECKING:
    from pathlib import Path


# lfc-missing skipping is centralised in tests/conftest.py
# (pytest_collection_modifyitems), which skips any lf-marked test.
pytestmark = pytest.mark.lf


def test_after_with_guard(tmp_path: Path) -> None:
    # The LF timer fires once and evaluates the guard at that instant.
    states = run_machine(
        tmp_path,
        FIXTURES_DIR / "after-guard",
        "AfterGuard::Machine",
        timeout="10 sec",
    )
    assert states == ["idle", "running", "done"]


def test_sm05_composite_state_var(tmp_path: Path) -> None:
    # Composite-attribute initializers (e.g. Point(x=0.5)) must serialize as
    # {= ... =} target code, and the Point dataclass ships in the companion
    # module via files:.
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm05-chained-references",
        "SM05::MachineChainGuard",
    )
    assert states == ["idle", "running"]


@pytest.mark.parametrize("support_kind", ["types", "python"])
def test_generated_support_runs_without_pythonpath(
    tmp_path: Path, support_kind: str
) -> None:
    if support_kind == "types":
        model = load_model(SM_EXAMPLES_DIR / "sm05-chained-references")
        program = build_program(model, "SM05::MachineChainGuard")
        assert program.types_module_name is not None
        program = replace(
            program,
            target_options=(
                *program.target_options,
                ("fast", "true"),
                ("timeout", "1 sec"),
            ),
        )
        python_file = None
    else:
        model_dir = SM_EXAMPLES_DIR / "part-external"
        python_file = model_dir / "bump.py"
        program = build_part_program(
            load_model(model_dir),
            "PartExt::counterSystem",
            target_options=(("fast", "true"), ("timeout", "1 sec")),
            external=[ForeignArtifact(python_file, "python")],
        )
    src = tmp_path / "src"
    src.mkdir()
    (src / "Main.lf").write_text(to_lf(program))
    if program.types_module_name is not None:
        (src / f"{program.types_module_name}.py").write_text(
            "\n".join(program.types_module_lines) + "\n"
        )
    if python_file is not None:
        shutil.copy(python_file, src / python_file.name)
    compile_result = subprocess.run(
        ["lfc", str(src / "Main.lf")], capture_output=True, timeout=600
    )
    assert compile_result.returncode == 0, compile_result.stderr.decode(
        errors="replace"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    process = subprocess.run(
        [str(tmp_path / "bin" / "Main")],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert process.returncode == 0, process.stderr


def test_rig_pair_passing_verdict(tmp_path: Path) -> None:
    process, states = run_rig(
        tmp_path, FIXTURES_DIR / "rig-pair", "RigPair::PlantRig"
    )
    assert process.returncode == 0, process.stderr
    # composite entry: the sub-state announcement overwrites "working" at the
    # same tag, so the first visible plant state after idle is "working.grind"
    assert states["plant"][0] == "idle"
    assert "working.grind" in states["plant"]
    assert states["tb"], "tb produced no announcements"
    assert states["tb"][-1] == "done"  # Done observed -> pass


def test_rig_pair_failing_verdict_aborts(tmp_path: Path) -> None:
    process, _states = run_rig(
        tmp_path,
        FIXTURES_DIR / "rig-pair",
        "RigPair::PlantRig",
        values={"RigPair::PlantTest": {"verdict": 1}},
    )
    assert process.returncode != 0
    assert "testPassed" in process.stderr


def test_rig_overlap_serves_local_and_peer(tmp_path: Path) -> None:
    process, states = run_rig(
        tmp_path, FIXTURES_DIR / "rig-overlap", "RigOverlap::PulserRig"
    )
    assert process.returncode == 0, process.stderr
    assert "sawTick" in states["tb"]  # peer port delivered
    # A `via` send does not loop back to its own sender: the plant's local
    # `accept Tick` is never served, so it does not reach `finished`.
    assert "finished" not in states["plant"]


def test_rig_payload_serves_local_and_peer(tmp_path: Path) -> None:
    # A ported send carrying a payload: the reaction parameter shadows the
    # preamble dataclass, so the constructor must be reached via globals().
    # Without the fix the set line raises TypeError.
    process, states = run_rig(
        tmp_path, FIXTURES_DIR / "rig-payload", "RigPayload::CounterRig"
    )
    assert process.returncode == 0, process.stderr
    assert states["tb"][-1] == "done"  # peer payload delivery worked
    # The plant's own overlap accept is no longer served by its `via` send.
    assert "finished" not in states["plant"]


def test_external_ramp_runs(tmp_path: Path) -> None:
    # `step` is supplied by ramp.py via the external build param; the
    # self-loop re-enters `run` every 0.1 s, re-announcing it. Proves the
    # external call compiles and runs (exit 0; every announcement is "run").
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm15-external",
        "SM15::Ramp",
        timeout="1 sec",
        python_file=SM_EXAMPLES_DIR / "sm15-external" / "ramp.py",
    )
    assert states, "no state announcements"
    assert all(s == "run" for s in states)


def test_rep_ramp_runs(tmp_path: Path) -> None:
    # Twin of test_external_ramp_runs: `step` comes from the calc def's
    # textual representation, so no --python module is supplied and the
    # generated Ramp_impl.py backs the call.
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm15-rep",
        "SM15Rep::Ramp",
        timeout="1 sec",
    )
    assert states, "no state announcements"
    assert all(s == "run" for s in states)


def test_part01_runs_and_logs(tmp_path: Path) -> None:
    logs, rc = run_part(
        tmp_path, SM_EXAMPLES_DIR / "part01-two-parts", "Part01::pingSystem"
    )
    assert rc == 0  # tester verdict ok (Pong arrived before the timeout)
    assert "entered Plant.pinged" in logs  # plant got the Ping (port-routed)
    assert "entered Tester.waitPong" in logs


def test_multi_exhibit_part_runs_and_passes_verdict(tmp_path: Path) -> None:
    # PartMulti::sys contains one part `rig : Rig` where Rig exhibits both
    # PlantBehavior and TesterBehavior. The tester sends Ping after 0.1 s,
    # the plant receives it (name-based cross-wiring inside Rig), replies
    # with Pong, and the tester reaches `done` (verdict == 0 → request_stop
    # → exit 0).  Verifies that compose_exhibits wires the internal signals
    # and the program compiles and runs cleanly.
    logs, rc = run_part(
        tmp_path,
        SM_EXAMPLES_DIR / "part-multi-exhibit",
        "PartMulti::sys",
        timeout="10 sec",
    )
    assert rc == 0, f"expected exit 0 (verdict pass); stderr:\n{logs}"


def test_part_external_with_python_runs(tmp_path: Path) -> None:
    # counterSystem has one part `c : Counter` whose CounterBehavior calls the
    # external calc def ``PartExt::bump``.  With bump.py supplied, the program
    # must compile and run cleanly (exit 0).
    logs, rc = run_part(
        tmp_path,
        SM_EXAMPLES_DIR / "part-external",
        "PartExt::counterSystem",
        python_file=SM_EXAMPLES_DIR / "part-external" / "bump.py",
        timeout="1 sec",
    )
    assert rc == 0, f"expected exit 0; stderr:\n{logs}"


def test_when_counter_fires_when_condition_reached(tmp_path: Path) -> None:
    # `n` starts 0, +1 on each idle entry; the 1-sec self-loop re-enters idle
    # until `n >= 3`, at which point the change trigger wins and completes.
    states = run_machine(
        tmp_path,
        FIXTURES_DIR / "when-counter",
        "WhenCounter::Machine",
        timeout="12 sec",
    )
    assert states[0] == "idle"
    assert "running" in states
    assert states[-1] == "done"


def test_when_assign_loop_terminates(tmp_path: Path) -> None:
    # A `when`-transition whose effect assigns a watched attribute must NOT
    # loop forever at frozen logical time (the gated change-notify fix). The
    # after-self-loop bumps `n` until `n >= 2`, then the change trigger fires,
    # assigns `n`, and completes -- quickly, not via the 120s subprocess kill.
    states = run_machine(
        tmp_path,
        FIXTURES_DIR / "when-assign-loop",
        "WhenAssignLoop::Machine",
        timeout="10 sec",
    )
    assert states[0] == "idle"
    assert "running" in states
    assert states[-1] == "done"


def test_part01_silent_without_debug(tmp_path: Path) -> None:
    # Without the run enabling DEBUG, the generated program emits no
    # observation lines (spec 5.5: silent unless the run turns DEBUG on).
    model = load_model(SM_EXAMPLES_DIR / "part01-two-parts")
    program = build_part_program(
        model,
        "Part01::pingSystem",
        target_options=(("fast", "true"), ("timeout", "10 sec")),
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "Main.lf").write_text(to_lf(program))
    if program.types_module_lines:
        assert program.types_module_name is not None
        (src / f"{program.types_module_name}.py").write_text(
            "\n".join(program.types_module_lines) + "\n"
        )
    compile_result = subprocess.run(
        ["lfc", str(src / "Main.lf")], capture_output=True, timeout=600
    )
    assert compile_result.returncode == 0, compile_result.stderr.decode(
        errors="replace"
    )
    process = subprocess.run(
        [str(tmp_path / "bin" / "Main")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0
    assert "entered" not in process.stderr
    assert "exited" not in process.stderr
