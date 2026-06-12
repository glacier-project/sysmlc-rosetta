from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import syside

from sysmlc.backends.rosetta.builder import RosettaBuilder, build_program
from sysmlc.backends.rosetta.composition import build_rig_program
from sysmlc.backends.rosetta.program import LfProgram
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.semantics.statemachine.interface import machine_interface
from sysmlc.sysml.loading import load_model
from sysmlc.sysml.queries import (
    exhibited_state_defs,
    resolve,
    rig_definitions,
)
from tests.backends.rosetta.conftest import FIXTURES_DIR
from tests.backends.showcase import SHOWCASE_DIR

if TYPE_CHECKING:
    from pathlib import Path


def test_rig_definitions_finds_the_rig() -> None:
    model = load_model(FIXTURES_DIR / "rig-pair")
    rigs = rig_definitions(model)
    assert [str(r.qualified_name) for r in rigs] == ["RigPair::PlantRig"]


def test_exhibited_state_defs_yields_named_pairs() -> None:
    model = load_model(FIXTURES_DIR / "rig-pair")
    rig = resolve(model, syside.PartDefinition, "RigPair::PlantRig")
    pairs = exhibited_state_defs(model, rig)
    assert [(name, str(sd.qualified_name)) for name, sd in pairs] == [
        ("plant", "RigPair::Plant"),
        ("tb", "RigPair::PlantTest"),
    ]


@pytest.mark.parametrize(
    ("rig_qn", "fragment"),
    [
        ("RigInvalid::ThreeExhibits", "exactly two"),
        ("RigInvalid::ExtraMember", "exhibit state"),
    ],
)
def test_malformed_rigs_are_rejected(rig_qn: str, fragment: str) -> None:
    model = load_model(FIXTURES_DIR / "rig-invalid")
    rig = resolve(model, syside.PartDefinition, rig_qn)
    with pytest.raises(ValueError, match=fragment):
        exhibited_state_defs(model, rig)


def test_interface_collects_accepts_and_sends() -> None:
    model = load_model(FIXTURES_DIR / "rig-pair")
    plant = machine_interface(model, "RigPair::Plant")
    assert plant.accepted == frozenset({"Go"})
    assert plant.sent == frozenset({"Done"})  # sent inside the composite
    tb = machine_interface(model, "RigPair::PlantTest")
    assert tb.accepted == frozenset({"Done"})
    assert tb.sent == frozenset({"Go"})


def _build_peer(
    model_dir: Path, qn: str, peer_accepts: frozenset[str]
) -> LfProgram:
    model = load_model(model_dir)
    name = qn.split("::")[-1]
    result = StateMachineDriver(model).run(
        qn, RosettaBuilder(name, peer_accepts=peer_accepts)
    )
    assert isinstance(result, LfProgram)
    return result


def test_default_peer_accepts_is_byte_identical() -> None:
    bare = to_lf(
        build_program(
            load_model(SHOWCASE_DIR / "microwave"), "Microwave::Microwave"
        )
    )
    explicit = to_lf(
        _build_peer(
            SHOWCASE_DIR / "microwave", "Microwave::Microwave", frozenset()
        )
    )
    assert bare == explicit


def test_peer_accepted_send_becomes_output_port() -> None:
    program = _build_peer(
        FIXTURES_DIR / "rig-pair", "RigPair::Plant", frozenset({"Done"})
    )
    machine = program.reactor
    assert "Done" in machine.outputs
    # The send happens inside `working`: its child reactor ports it too.
    child = next(r for r in program.reactors if r.name == "Plant_working")
    assert "Done" in child.outputs
    # ... and the machine re-emits the child's port upward.
    text = to_lf(program)
    assert "Done.set(c_working.Done.value)" in text
    assert "Done.set(True)" in text  # the send renders set(...)
    assert "Done_act" not in text  # port-only: no self-event


def test_peer_sent_signal_loses_its_input_port() -> None:
    program = _build_peer(
        FIXTURES_DIR / "rig-pair",
        "RigPair::PlantTest",
        frozenset({"Go"}),
    )
    machine = program.reactor
    assert "Go" in machine.outputs
    assert "Go" not in machine.inputs  # inputs = accepted - peer-sent
    assert "Done" in machine.inputs


def test_overlap_send_emits_both_forms() -> None:
    program = _build_peer(
        FIXTURES_DIR / "rig-overlap",
        "RigOverlap::Pulser",
        frozenset({"Tick"}),
    )
    machine = program.reactor
    assert "Tick" in machine.outputs
    assert "Tick" not in machine.inputs
    text = to_lf(program)
    assert "Tick.set(True)" in text
    assert "Tick_act.schedule(0)" in text  # local accept still served


def test_rig_program_composes_bench_reactor() -> None:
    model = load_model(FIXTURES_DIR / "rig-pair")
    program = build_rig_program(model, "RigPair::PlantRig")
    bench = program.reactor
    assert bench.name == "PlantRig"
    assert [i.name for i in bench.instantiations] == ["plant", "tb"]
    assert [i.reactor for i in bench.instantiations] == [
        "Plant",
        "PlantTest",
    ]
    connections = {(c.source, c.target) for c in bench.connections}
    assert ("plant.Done", "tb.Done") in connections
    assert ("tb.Go", "plant.Go") in connections
    assert ("plant.current_state", "plant_current_state") in connections
    assert ("tb.current_state", "tb_current_state") in connections
    assert set(bench.outputs) == {
        "plant_current_state",
        "tb_current_state",
    }
    # Machine families both present, bench last.
    names = [r.name for r in program.reactors]
    assert names[-1] == "PlantRig"
    assert "Plant" in names and "PlantTest" in names


def test_rig_with_same_def_twice_is_rejected() -> None:
    model = load_model(FIXTURES_DIR / "rig-invalid")
    with pytest.raises(UnsupportedConstructError, match="twice"):
        build_rig_program(model, "RigInvalid::Twice")


def test_signal_sent_by_both_machines_is_rejected() -> None:
    model = load_model(FIXTURES_DIR / "rig-invalid")
    with pytest.raises(UnsupportedConstructError, match="both machines"):
        build_rig_program(model, "RigInvalid::BothSend")


def test_rig_pair_serializes_to_lf() -> None:
    model = load_model(FIXTURES_DIR / "rig-pair")
    text = to_lf(build_rig_program(model, "RigPair::PlantRig"))
    assert "reactor PlantRig {" in text
    assert "main reactor {" in text
    assert text.index("reactor Plant ") < text.index("reactor PlantRig")
    assert "m = new PlantRig()" in text
