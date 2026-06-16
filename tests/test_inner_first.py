from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sismic.interpreter import Interpreter
from sismic.io import import_from_yaml

from sysmlc.backends.quake.backend import QuakeBackend
from sysmlc.backends.quake.serialize import to_yaml as quake_to_yaml
from sysmlc.backends.rosetta.builder import RosettaBuilder, build_program
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR
from tests.backends.rosetta.test_run import run_machine

MODEL_DIR = FIXTURES_DIR / "inner-first"


def _builder(qn: str) -> RosettaBuilder:
    """Drive a full build and hand back the populated builder.

    The detection maps are filled during ``result()`` (inside ``.run``) and
    persist on the instance, so we keep our own reference rather than letting
    ``build_program`` own the builder.
    """
    model = load_model(MODEL_DIR)
    builder = RosettaBuilder(qn.split("::")[-1])
    StateMachineDriver(model).run(qn, builder)
    return builder


def test_direct_conflict_detected() -> None:
    b = _builder("InnerFirst::M")
    assert "Ev" in b._consumed.get("outer", {})
    assert b._guarded_interrupts.get("outer", {}).get("Ev") == ["outer"]


def test_deep_conflict_propagates() -> None:
    b = _builder("InnerFirst::MDeep")
    assert "Ev" in b._consumed.get("outer::mid", {})
    assert "Ev" in b._consumed.get("outer", {})
    assert b._guarded_interrupts.get("outer", {}).get("Ev") == ["outer"]


def test_parallel_conflict_reads_consuming_region() -> None:
    b = _builder("InnerFirst::MPar")
    assert "Ev" in b._consumed.get("region::regA", {})
    assert b._guarded_interrupts.get("region", {}).get("Ev") == ["region::regA"]


def test_sibling_composites_are_not_a_conflict() -> None:
    b = _builder("InnerFirst::MSibling")
    assert b._guarded_interrupts == {}
    assert b._consumed == {}


def _quake_config_after_ev(qn: str) -> set[str]:
    """Build qn through quake, run it through sismic, fire one Ev."""
    model = load_model(MODEL_DIR)
    chart = QuakeBackend().build(model, qn)
    with tempfile.TemporaryDirectory() as d:
        yaml_path = Path(d) / "chart.yaml"
        yaml_path.write_text(quake_to_yaml(chart))
        sc = import_from_yaml(filepath=str(yaml_path))
    intp = Interpreter(sc)
    intp.execute()
    intp.queue("Ev")
    intp.execute()
    return set(intp.configuration)


EV_AT_100MS = (
    "  timer ev(100 msec)\n  reaction(ev) -> m.Ev {=\n    m.Ev.set(True)\n  =}"
)


def test_direct_structural_has_guarded_interrupt() -> None:
    lf = to_lf(build_program(load_model(MODEL_DIR), "InnerFirst::M"))
    assert "output Ev_consumed" in lf
    assert "reaction(Ev, c_outer.Ev_consumed) -> reset(aborted)" in lf
    assert "if not (c_outer.Ev_consumed.is_present):" in lf


@pytest.mark.lf
def test_direct_inner_first_matches_quake(tmp_path: Path) -> None:
    rosetta = run_machine(
        tmp_path, MODEL_DIR, "InnerFirst::M", drivers=EV_AT_100MS
    )
    quake = _quake_config_after_ev("InnerFirst::M")
    # Same source, same result: inner-first, neither aborts.
    assert rosetta[-1] == "outer.innerB"
    assert rosetta[-1].replace(".", "::") in quake
    assert "aborted" not in quake


@pytest.mark.lf
def test_deep_inner_first_matches_quake(tmp_path: Path) -> None:
    rosetta = run_machine(
        tmp_path, MODEL_DIR, "InnerFirst::MDeep", drivers=EV_AT_100MS
    )
    quake = _quake_config_after_ev("InnerFirst::MDeep")
    assert rosetta[-1] == "outer.mid.innerB"
    assert rosetta[-1].replace(".", "::") in quake
    assert "aborted" not in quake
