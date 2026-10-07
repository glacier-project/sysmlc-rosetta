from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sysmlc_models.catalog import iter_models, model_path
from sysmlc_models.scenarios import SCENARIOS
from sysmlc_models.validation import (
    Scenario,
    validate_rosetta_inertness,
    validate_scenario,
)

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


@pytest.mark.parametrize(
    "name",
    [
        str(directory.relative_to(model_path("showcase")))
        for directory in iter_models("showcase")
    ],
)
def test_shared_showcase_inertness(name: str, tmp_path: Path) -> None:
    validate_rosetta_inertness(f"showcase/{name}", tmp_path)
