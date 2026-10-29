"""Execute constant bindings and reassigned initial values in generated LF."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import FIXTURES_DIR
from tests.test_lf_harness import run_machine

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.lf


def test_constants_stay_readable_while_initials_change(
    tmp_path: Path,
) -> None:
    # The guards only reach `finished` when a == 5 after its assignment and
    # every constant still holds its bound literal.
    states = run_machine(
        tmp_path, FIXTURES_DIR / "bindings", "Bindings::Machine"
    )
    assert states == ["idle", "checked", "finished"]


def test_configured_bound_default_reaches_the_guard(tmp_path: Path) -> None:
    # seen = 5 + 1 + 4 with the configured default, so the guard expecting
    # the model's 3 takes the discriminating branch.
    states = run_machine(
        tmp_path,
        FIXTURES_DIR / "bindings",
        "Bindings::Machine",
        values={"limit": 4},
    )
    assert states == ["idle", "checked", "wrong"]
