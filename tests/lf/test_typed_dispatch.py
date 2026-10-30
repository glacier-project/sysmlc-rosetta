"""Native typed signal dispatch and concrete route controls."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.codegen import PreambleNeeds
from sysmlc_rosetta.parts import compose_exhibits
from tests.conftest import FIXTURES_DIR
from tests.test_lf_harness import run_machine, run_part

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.lf
FIXTURE = FIXTURES_DIR / "typed-dispatch"


def test_external_only_subtype_with_inherited_field(tmp_path: Path) -> None:
    program = build_program(load_model(FIXTURE), "TypedDispatch::External")
    assert program.resolve_signal("TypedDispatch::Child") == "Child"
    assert "class Child:" in program.types_module_lines
    states = run_machine(
        tmp_path,
        FIXTURE,
        "TypedDispatch::External",
        drivers="""  timer deliver(100 msec)
  reaction(deliver) -> m.Child {=
    import types
    m.Child.set(types.SimpleNamespace(value=41))
  =}""",
    )
    assert states == ["waiting", "received", "verified"]


def test_qualified_same_name_inputs_remain_distinct(tmp_path: Path) -> None:
    program = build_program(load_model(FIXTURE), "TypedDispatch::Collisions")
    a = program.resolve_signal("TypedDispatch::A::Alarm")
    b = program.resolve_signal("TypedDispatch::B::Alarm")
    assert a != b
    with pytest.raises(ValueError, match="ambiguous"):
        program.resolve_signal("Alarm")
    # Deliver B first: it must not satisfy the first A accept. Later A/B
    # deliveries must each retain their own payload values.
    drivers = []
    for index, (channel, value) in enumerate(((b, 99), (a, 17), (b, 29))):
        drivers.append(
            f"  timer deliver{index}({(index + 1) * 100} msec)\n"
            f"  reaction(deliver{index}) -> m.{channel} {{=\n"
            "    import types\n"
            f"    m.{channel}.set(types.SimpleNamespace(value={value}))\n"
            "  =}"
        )
    states = run_machine(
        tmp_path,
        FIXTURE,
        "TypedDispatch::Collisions",
        drivers="\n".join(drivers),
    )
    assert states == ["waiting", "firstReceived", "secondReceived", "verified"]


def test_inner_derived_accept_suppresses_outer_base(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path,
        FIXTURE,
        "TypedDispatch::Nested",
        drivers="""  timer deliver(100 msec)
  reaction(deliver) -> m.Child {=
    m.Child.set(True)
  =}""",
    )
    assert states == ["outer.inner", "outer.selected"]


def test_peer_routes_deliver_once_only_to_compatible_receivers(
    tmp_path: Path,
) -> None:
    model = load_model(FIXTURE)
    _, composite = compose_exhibits(
        model,
        "Peers",
        (
            ("sender", "TypedDispatch::Sender"),
            ("receiver", "TypedDispatch::Receiver"),
            ("unrelated", "TypedDispatch::UnrelatedReceiver"),
        ),
        PreambleNeeds(),
    )
    routes = [(c.source, c.target) for c in composite.connections]
    assert routes.count(("sender.Child", "receiver.Child")) == 1
    assert ("sender.Child", "unrelated.Child") not in routes
    logs, returncode = run_part(
        tmp_path, FIXTURE, "TypedDispatch::system", timeout="1 sec"
    )
    assert returncode == 0, logs
    assert "entered ReceiverPart.verified" in logs, logs
    assert "entered ReceiverPart.duplicate" not in logs
    assert "entered UnrelatedPart.wrong" not in logs


def test_qualified_constructors_use_distinct_class_symbols(
    tmp_path: Path,
) -> None:
    states = run_machine(
        tmp_path, FIXTURE, "TypedDispatch::ConstructCollisions"
    )
    assert states == ["idle", "waitingA", "waitingB", "received", "verified"]


@pytest.mark.parametrize(
    ("machine", "selected"),
    [
        ("NestedDeclaredFirst", "localSelected"),
        ("ParentDeclaredFirst", "parentSelected"),
    ],
)
def test_same_source_priority_uses_source_declaration_order(
    tmp_path: Path,
    machine: str,
    selected: str,
) -> None:
    states = run_machine(
        tmp_path,
        FIXTURE,
        f"TypedDispatch::{machine}",
        drivers="""  timer deliver(100 msec)
  reaction(deliver) -> m.Child {=
    m.Child.set(True)
  =}""",
    )
    assert states == ["outer.inner", f"outer.{selected}"]
