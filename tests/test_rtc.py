from __future__ import annotations

import pytest

from sysmlc.backends.rosetta.builder import build_program
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR

MODEL_DIR = FIXTURES_DIR / "rtc"  # created in Task 5


def test_multi_trigger_emits_fired_flag() -> None:
    lf = to_lf(build_program(load_model(MODEL_DIR), "Rtc::TwoSignals"))
    # The per-mode flag is declared (plain state, not `reset state`).
    assert "state idle_fired = {= False =}" in lf
    assert "reset state idle_fired" not in lf
    # Reset in the entry reaction body.
    assert "self.idle_fired = False" in lf
    # Each transition body is guarded by the flag.
    assert "if not self.idle_fired:" in lf
    assert "self.idle_fired = True" in lf


def test_multi_trigger_reactions_in_declaration_order() -> None:
    lf = to_lf(build_program(load_model(MODEL_DIR), "Rtc::TwoSignals"))
    # A is declared before B, so reaction(A) precedes reaction(B).
    assert lf.index("reaction(A)") < lf.index("reaction(B)")


def test_single_trigger_has_no_fired_flag() -> None:
    # `stopped` accepts StartCmd + ResetCmd -> multi; but a single-trigger
    # state must not gain the flag.  Use a known single-trigger machine.
    lf = to_lf(build_program(load_model(MODEL_DIR), "Rtc::SingleTrigger"))
    assert "_fired" not in lf


def test_fired_name_collision_is_rejected() -> None:
    with pytest.raises(UnsupportedConstructError, match="collides"):
        build_program(load_model(MODEL_DIR), "Rtc::NameClashFired")
