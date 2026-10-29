from pathlib import Path

import pytest
import syside
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model
from sysmlc.sysml.queries import resolve

from sysmlc_rosetta.builder import build_program


@pytest.fixture(scope="module")
def phase_model() -> syside.Model:
    return load_model(Path(__file__).parent / "fixtures" / "phase-actions")


@pytest.mark.parametrize("phase", ["entry", "exit", "effect", "do"])
@pytest.mark.parametrize(
    "form", ["Reference", "ReferenceLocal", "Subsetting", "Typed", "TypedLocal"]
)
def test_referenced_phase_action_is_rejected(
    phase_model: syside.Model, phase: str, form: str
) -> None:
    # Valid SysML actions must fail explicitly, including typed wrappers
    # whose local statements cannot replace the inherited action body.
    with pytest.raises(UnsupportedConstructError, match=phase) as error:
        build_program(phase_model, f"PhaseActions::{phase.title()}{form}")
    machine = resolve(
        phase_model,
        syside.StateDefinition,
        f"PhaseActions::{phase.title()}{form}",
    )
    if phase == "effect":
        slot = machine.owned_transitions.collect()[0].effect_action
    else:
        slot = getattr(machine.owned_states.collect()[0], f"{phase}_action")
    assert error.value.node is slot
