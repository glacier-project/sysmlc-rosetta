"""Execute constructed send payloads natively with lfc.

Each machine reaches ``verified`` only when the receiver observed the
expected field values, so the state trace checks the delivered payload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import FIXTURES_DIR
from tests.test_lf_harness import run_machine, run_part

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.lf

FIXTURE = FIXTURES_DIR / "payload-construction"


def test_arguments_are_evaluated_once_at_the_send_in_written_order(
    tmp_path: Path,
) -> None:
    # `payload_evaluations.sample` counts its calls: y = 401 and x = 402
    # only if each argument is evaluated once, in the written order, while
    # `current` is 40; `evaluations()` must then report two calls.
    states = run_machine(
        tmp_path,
        FIXTURE,
        "PayloadConstruction::EvaluationOnce",
        python_file=FIXTURE / "payload_evaluations.py",
    )

    assert states == ["idle", "armed", "received", "verified"]


def test_inherited_structured_payload_carries_its_type_values(
    tmp_path: Path,
) -> None:
    states = run_machine(
        tmp_path, FIXTURE, "PayloadConstruction::InheritedStructured"
    )

    assert states == ["idle", "armed", "received", "verified"]


def test_connected_peer_receives_the_constructed_values(
    tmp_path: Path,
) -> None:
    logs, returncode = run_part(
        tmp_path, FIXTURE, "PayloadConstruction::peerSystem", timeout="1 sec"
    )

    assert returncode == 0, logs
    assert "entered PeerSender.sent" in logs
    assert "entered PeerReceiver.verified" in logs, logs
    assert "entered DefaultReceiver.verified" in logs, logs
