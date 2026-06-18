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
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR
from tests.backends.rosetta.test_lf_harness import run_machine

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


def test_parallel_structural_ors_regions() -> None:
    lf = to_lf(build_program(load_model(MODEL_DIR), "InnerFirst::MPar"))
    assert "reaction(Ev, c_regA.Ev_consumed) -> reset(aborted)" in lf
    assert "if not (c_regA.Ev_consumed.is_present):" in lf


@pytest.mark.lf
def test_parallel_inner_first_matches_quake(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path,
        MODEL_DIR,
        "InnerFirst::MPar",
        drivers=EV_AT_100MS,
        timeout="1 sec",
    )
    # regA consumed Ev (a1 -> a2); the parallel group interrupt is suppressed.
    assert any(s.startswith("region.regA.a2") for s in states)
    assert "aborted" not in states


# ---------------------------------------------------------------------------
# MParDeep: parallel nested inside a conflicting composite (Task 4 gap)
# ---------------------------------------------------------------------------


def test_par_deep_conflict_propagates_to_outer() -> None:
    """White-box: consumed flag propagates from regA through `region` to outer.

    This exercises the gate ``sig in self._consumed.get(scope, {})`` in the
    PARALLEL branch of ``_child_mode``.
    """
    b = _builder("InnerFirst::MParDeep")
    # The outer reactor must carry the consumed flag (enables the per-region
    # re-emit in _child_mode's PARALLEL branch).
    assert "Ev" in b._consumed.get("outer", {})
    assert b._guarded_interrupts.get("outer", {}).get("Ev") == ["outer"]


def test_par_deep_structural_has_per_region_reemit() -> None:
    """Structural: the per-region re-emit reaction appears in generated LF."""
    lf = to_lf(build_program(load_model(MODEL_DIR), "InnerFirst::MParDeep"))
    # _port_reemit emits this reaction body in MParDeep_outer's region mode.
    assert "Ev_consumed.set(c_regA.Ev_consumed.value)" in lf
    # The outer group-interrupt reaction is also present and guarded.
    assert "reaction(Ev, c_outer.Ev_consumed) -> reset(aborted)" in lf
    assert "if not (c_outer.Ev_consumed.is_present):" in lf


@pytest.mark.lf
def test_par_deep_inner_first_matches_quake(tmp_path: Path) -> None:
    """End-to-end: regA takes a1->a2; outer group interrupt is suppressed."""
    states = run_machine(
        tmp_path,
        MODEL_DIR,
        "InnerFirst::MParDeep",
        drivers=EV_AT_100MS,
        timeout="1 sec",
    )
    quake = _quake_config_after_ev("InnerFirst::MParDeep")
    # Rosetta: regA settled in a2 (observed: ['outer.region.regB.b1',
    # 'outer.region.regA.a2']); outer group interrupt never fired.
    assert any(s.endswith("regA.a2") for s in states)
    assert "aborted" not in states
    # Quake agrees: a2 leaf present, aborted absent.
    assert "outer::region::regA::a2" in quake
    assert not any("aborted" in c for c in quake)


# ---------------------------------------------------------------------------
# MNameClash: state named `Ev_consumed` collides with the generated port
# ---------------------------------------------------------------------------


def test_consumed_name_collision_is_rejected() -> None:
    with pytest.raises(UnsupportedConstructError, match="collides"):
        build_program(load_model(MODEL_DIR), "InnerFirst::MNameClash")


# ---------------------------------------------------------------------------
# MOuterFires: inner guard is false → outer group interrupt fires (1A gap)
# ---------------------------------------------------------------------------


def test_outer_fires_conflict_detected() -> None:
    """White-box: guarded inner transition still triggers conflict detection.

    Detection keys on the trigger, not the guard; Ev_consumed plumbing is
    generated even though the inner branch can never execute.
    """
    b = _builder("InnerFirst::MOuterFires")
    assert "Ev" in b._consumed.get("outer", {})
    assert b._guarded_interrupts.get("outer", {}).get("Ev") == ["outer"]
    lf = to_lf(build_program(load_model(MODEL_DIR), "InnerFirst::MOuterFires"))
    assert "Ev_consumed" in lf


@pytest.mark.lf
def test_outer_fires_when_inner_guard_false(tmp_path: Path) -> None:
    """End-to-end: guard-false inner transition never consumes Ev; outer fires.

    Both rosetta and quake must reach ``aborted``.  This is the symmetric
    direction of the inner-first tests: confirms the guard cannot be
    accidentally inverted or stuck.
    """
    rosetta = run_machine(
        tmp_path, MODEL_DIR, "InnerFirst::MOuterFires", drivers=EV_AT_100MS
    )
    quake = _quake_config_after_ev("InnerFirst::MOuterFires")
    # Outer group interrupt fired; aborted is the final state.
    assert rosetta[-1] == "aborted"
    assert "aborted" in quake
    # Inner transition never fired (guard was false).
    assert "innerB" not in rosetta
    assert not any("innerB" in c for c in quake)
