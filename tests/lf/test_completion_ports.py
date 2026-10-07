from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from tests.conftest import FIXTURES_DIR
from tests.test_lf_harness import run_machine

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.lf


@pytest.mark.parametrize(
    ("directory", "qn", "prefix"),
    [
        (SM_EXAMPLES_DIR / "sm10-done", "SM10::MachinePartialJoin", "working."),
        (FIXTURES_DIR / "partial-parallel", "PartialParallel::Machine", ""),
    ],
)
def test_unwritten_completion_port_does_not_abort_or_complete(
    directory: Path,
    qn: str,
    prefix: str,
    tmp_path: Path,
) -> None:
    states = run_machine(
        tmp_path,
        directory,
        qn,
        timeout="1 sec",
        drivers=(
            "  timer a(100 msec)\n"
            "  reaction(a) -> m.EventA {= m.EventA.set(True) =}\n"
            "  timer b(200 msec)\n"
            "  reaction(b) -> m.EventB {= m.EventB.set(True) =}"
        ),
    )
    # Region a completes, while b must remain active and receive a later
    # event. Reading its unwritten LF port used to abort at a's completion.
    assert f"{prefix}a.done" in states
    assert f"{prefix}b.b2" in states
    assert "finished" not in states
