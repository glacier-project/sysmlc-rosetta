from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sysmlc_models.scenarios import SCENARIOS
from sysmlc_models.validation import Scenario, validate_scenario

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.lf


@pytest.mark.parametrize(
    "scenario",
    [s for s in SCENARIOS if "rosetta" in s.backends],
    ids=lambda s: s.name,
)
def test_shared_model_scenario(scenario: Scenario, tmp_path: Path) -> None:
    validate_scenario(scenario, "rosetta", tmp_path)
