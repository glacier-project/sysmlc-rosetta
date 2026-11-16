"""Run per-write constraint checks: an in-between state aborts the run."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.conftest import FIXTURES_DIR
from tests.test_lf_harness import compile_harness, run_machine

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.lf

_FIXTURE = FIXTURES_DIR / "per-write-checks"


def test_in_between_state_aborts_naming_the_constraint(
    tmp_path: Path,
) -> None:
    # low := 10 from (0, 5) leaves low > high until high := 20 runs.
    binary = compile_harness(tmp_path, _FIXTURE, "PerWriteChecks::Pair")
    result = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(tmp_path / "src")},
    )
    assert result.returncode != 0
    assert "AssertionError" in result.stderr
    assert "inOrder" in result.stderr


def test_ordering_the_writes_runs_clean(tmp_path: Path) -> None:
    states = run_machine(tmp_path, _FIXTURE, "PerWriteChecks::PairOk")
    assert states == ["idle", "finished"]
