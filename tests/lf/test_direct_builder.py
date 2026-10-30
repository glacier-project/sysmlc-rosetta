"""Execute catalog-free public builder construction paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.backend import RosettaBackend
from sysmlc_rosetta.builder import RosettaBuilder
from tests.conftest import FIXTURES_DIR
from tests.test_lf_harness import run_machine

if TYPE_CHECKING:
    from pathlib import Path

    import syside

pytestmark = pytest.mark.lf


@pytest.mark.parametrize(
    ("directory", "qn", "expected"),
    [
        (
            "sm20-typed-signals",
            "SM20::MachineExact",
            ["idle", "armed", "received"],
        ),
        (
            "sm18-enum-literals",
            "SM18::MachineStringEnum",
            ["idle", "green", "matched"],
        ),
        (
            "sm05-chained-references",
            "SM05::MachineChainGuard",
            ["idle", "running"],
        ),
    ],
)
def test_direct_builder_types_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory: str,
    qn: str,
    expected: list[str],
) -> None:
    def direct_build(
        _backend: object,
        model: syside.Model,
        element_qn: str,
        **_kwargs: object,
    ) -> object:
        return StateMachineDriver(model).run(
            element_qn, RosettaBuilder(element_qn.rsplit("::", 1)[-1])
        )

    # Reuse the normal compile/run harness while substituting the direct
    # public builder construction, which deliberately has no PreambleNeeds.
    monkeypatch.setattr(RosettaBackend, "build", direct_build)
    states = run_machine(tmp_path, SM_EXAMPLES_DIR / directory, qn)
    assert states == expected


def test_direct_builder_ported_payload_uses_unshadowed_class(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def direct_build(
        _backend: object,
        model: syside.Model,
        element_qn: str,
        **_kwargs: object,
    ) -> object:
        return StateMachineDriver(model).run(
            element_qn,
            RosettaBuilder("Sender", peer_accepts=frozenset({"Child"})),
        )

    monkeypatch.setattr(RosettaBackend, "build", direct_build)
    states = run_machine(
        tmp_path,
        FIXTURES_DIR / "typed-dispatch",
        "TypedDispatch::Sender",
        drivers="""  state deliveries = {= 0 =}
  reaction(m.Child) {=
    assert m.Child.value.value == 41
    self.deliveries += 1
  =}
  reaction(shutdown) {=
    assert self.deliveries == 1
  =}""",
    )
    assert states == ["idle", "sent"]
