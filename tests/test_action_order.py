from pathlib import Path

import pytest
import syside
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program
from tests.test_lf_harness import run_machine

FIXTURES = Path(__file__).parent / "fixtures" / "action-order"


@pytest.fixture(scope="module")
def order_model() -> syside.Model:
    return load_model(FIXTURES)


@pytest.mark.parametrize(
    "name",
    [
        "Partial",
        "Disconnected",
        "WrongStart",
        "WrongDone",
        "Branch",
        "Merge",
        "Cycle",
        "Guarded",
        "Chained",
        "Foreign",
        "ShadowStart",
        "Multiplicity",
        "EndMultiplicity",
        "ActionMultiplicity",
        "SuccessionFlow",
    ],
)
def test_unsupported_sequence_is_rejected(
    order_model: syside.Model, name: str
) -> None:
    with pytest.raises(
        UnsupportedConstructError, match=r"succession|sequence|multiplicity"
    ) as error:
        build_program(order_model, f"ActionOrder::{name}")
    assert error.value.node is not None


@pytest.mark.lf
@pytest.mark.parametrize(
    "phase",
    [
        "Entry",
        "Exit",
        "Do",
        "Effect",
        "Declaration",
        "Then",
        "Boundaries",
        "Send",
    ],
)
def test_action_order_executes(tmp_path: Path, phase: str) -> None:
    # Each model reaches success only for its independently specified value:
    # declaration order gives 1, explicit initialization then multiplication 10.
    assert run_machine(tmp_path, FIXTURES, f"ActionOrder::Runtime{phase}") == [
        "idle",
        "checked",
        "success",
    ]
