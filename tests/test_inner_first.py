from __future__ import annotations

from sysmlc.backends.rosetta.builder import RosettaBuilder
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR

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
