from __future__ import annotations

import pytest
import syside

from sysmlc.sysml.loading import load_model
from sysmlc.sysml.queries import (
    exhibited_state_defs,
    resolve,
    rig_definitions,
)
from tests.backends.rosetta.conftest import FIXTURES_DIR


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
