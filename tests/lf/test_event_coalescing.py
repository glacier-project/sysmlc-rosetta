"""Modeled-event port writes reject silent same-tag overwrites."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import FIXTURES_DIR
from tests.test_lf_harness import run_machine, run_part

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.lf
FIXTURE = FIXTURES_DIR / "event-coalescing"


@pytest.mark.parametrize("system", ["direct", "siblings"])
def test_same_tag_modeled_event_overwrite_is_diagnosed(
    tmp_path: Path, system: str
) -> None:
    logs, code = run_part(
        tmp_path, FIXTURE, f"EventCoalescing::{system}", timeout="150 msec"
    )
    assert code != 0
    assert "RuntimeError: multiple modeled events on LF port Pulse" in logs
    assert "event coalescing is not supported" in logs


def test_separate_microstep_deliveries_keep_payloads(tmp_path: Path) -> None:
    logs, code = run_part(
        tmp_path, FIXTURE, "EventCoalescing::separated", timeout="1 msec"
    )
    assert code == 0, logs
    assert "entered Sink.second" in logs
    assert "entered Sink.verified" in logs


def test_self_send_remains_a_scheduled_action(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path, FIXTURE, "EventCoalescing::SelfSend", timeout="150 msec"
    )
    assert states == ["idle", "waitingFirst", "second", "verified"]
