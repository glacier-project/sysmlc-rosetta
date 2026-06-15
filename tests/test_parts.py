from pathlib import Path

import pytest

from sysmlc.backends.rosetta.parts import build_part_program
from sysmlc.backends.rosetta.program import LfProgram, MainReactor
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model

FIX = Path("models/sm-examples/part01-two-parts")
MUX = Path("models/sm-examples/part-mux")
UNDECLARED_VIA = Path("models/sm-examples/part-undeclared-via")


def test_build_part_program_composes_two_parts() -> None:
    prog = build_part_program(load_model(FIX), "Part01::pingSystem")
    assert isinstance(prog, LfProgram)
    assert isinstance(prog.main, MainReactor)
    names = {r.name for r in prog.reactors}
    assert {"Plant", "Tester"} <= names  # one reactor per part def
    insts = {(i.name, i.reactor) for i in prog.main.instantiations}
    assert insts == {("plant", "Plant"), ("tb", "Tester")}
    text = to_lf(prog)
    # Ping flows tester->plant, Pong flows plant->tester (port-based routing).
    assert "tb.Ping -> plant.Ping" in text
    assert "plant.Pong -> tb.Pong" in text


def test_routing_is_port_based_not_name_based() -> None:
    # Hub sends M via port `a` only. Both sinks accept M; same-name routing
    # would fan M out to BOTH. Port-based routing wires only the sink on the
    # sending port.
    prog = build_part_program(load_model(MUX), "PartMux::mux")
    text = to_lf(prog)
    assert "hub.M -> s1.M" in text  # connected via the sending port `a`
    assert "hub.M -> s2.M" not in text  # NOT sent via `b`


def test_undeclared_via_port_is_rejected() -> None:
    model = load_model(UNDECLARED_VIA)
    with pytest.raises(UnsupportedConstructError, match="does not declare"):
        build_part_program(model, "PartUV::sys")
