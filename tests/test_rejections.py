from __future__ import annotations

import pytest

from sysmlc.backends.rosetta.builder import RosettaBuilder, build_program
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.statemachine.facts import (
    AttributeBinding,
    AttributeDirection,
    StateFact,
    StateKind,
    TransitionFact,
)
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR
from tests.backends.test_sm_examples import SM_EXAMPLES_DIR


def _leaf(name: str, parent: str = "Machine") -> StateFact:
    return StateFact(
        name=name,
        kind=StateKind.LEAF,
        parent=parent,
        initial_substate=None,
        entry_action=None,
        do_action=None,
        exit_action=None,
    )


def _root(name: str = "Machine", initial: str = "idle") -> StateFact:
    return StateFact(
        name=name,
        kind=StateKind.COMPOSITE,
        parent=None,
        initial_substate=initial,
        entry_action=None,
        do_action=None,
        exit_action=None,
    )


def test_deep_entry_is_rejected() -> None:
    # `transition first idle then running.hot` enters a composite from
    # outside; hierarchy itself is supported since slice 6.
    model = load_model(SM_EXAMPLES_DIR / "sm08-nested-composite")
    with pytest.raises(UnsupportedConstructError, match="deep entry"):
        build_program(model, "SM08::MachineCrossIn")


def test_leaf_parallel_region_is_rejected() -> None:
    builder = RosettaBuilder("Machine")
    builder.add_state(
        StateFact(
            name="Machine",
            kind=StateKind.PARALLEL,
            parent=None,
            initial_substate=None,
            entry_action=None,
            do_action=None,
            exit_action=None,
        )
    )
    builder.add_state(_leaf("lights"))
    with pytest.raises(UnsupportedConstructError, match="region"):
        builder.result()


def test_scoped_constraint_is_rejected() -> None:
    # Substate reactors cannot see the machine's attributes, so a
    # state-scoped asserted constraint has nothing meaningful to check.
    model = load_model(FIXTURES_DIR / "scoped-constraint")
    with pytest.raises(UnsupportedConstructError, match="root-scope"):
        build_program(model, "ScopedConstraint::Machine")


def test_cross_scope_send_is_rejected() -> None:
    # A substate's entry sends Ping, but Ping is accepted by a group
    # interrupt handled in the root scope: the event would have to cross
    # reactor boundaries, which rosetta does not route yet.
    model = load_model(FIXTURES_DIR / "cross-scope-send")
    with pytest.raises(UnsupportedConstructError, match="cross-scope"):
        build_program(model, "CrossScopeSend::Machine")


def test_payload_writeback_is_rejected() -> None:
    # Payload locals are read-only: `local_names` applies to expression
    # rendering, never to assignment targets, so `assign r.value := 0`
    # fails on the unknown base name `r`.
    model = load_model(FIXTURES_DIR / "payload-guard")
    with pytest.raises(UnsupportedConstructError, match="'r'"):
        build_program(model, "PayloadGuard::MachineWriteBack")


def test_payload_referencing_guard_builds_and_binds() -> None:
    # A guard that reads the accept payload (``if r.value > 0``) must now
    # build successfully: the payload name is added as a local and a binding
    # line ``r = Reading.value`` is prepended to the reaction body.
    model = load_model(FIXTURES_DIR / "payload-guard")
    program = build_program(model, "PayloadGuard::Machine")
    (idle,) = [m for m in program.reactor.modes if m.name == "idle"]
    reading_reaction = idle.reactions[1]
    assert reading_reaction.body[0] == "r = Reading.value"
    assert reading_reaction.body[1] == "if r.value > 0:"


def test_scoped_attribute_is_rejected() -> None:
    builder = RosettaBuilder("Machine")
    with pytest.raises(UnsupportedConstructError, match="root-scope"):
        builder.bind_attribute(
            AttributeBinding(scope="idle", name="x", value=None)
        )


def test_state_named_done_is_rejected() -> None:
    builder = RosettaBuilder("Machine")
    builder.add_state(_root(initial="done"))
    builder.add_state(_leaf("done"))
    with pytest.raises(UnsupportedConstructError, match="collides"):
        builder.result()


def test_unstable_self_loop_is_rejected() -> None:
    builder = RosettaBuilder("Machine")
    builder.add_state(_root())
    builder.add_state(_leaf("idle"))
    builder.add_transition(
        TransitionFact(
            source="idle",
            target="idle",
            trigger=None,
            guard=None,
            effect=None,
        )
    )
    with pytest.raises(UnsupportedConstructError, match="stabilize"):
        builder.result()


def test_in_attribute_without_default_is_rejected() -> None:
    builder = RosettaBuilder("Machine")
    builder.add_state(_root())
    builder.add_state(_leaf("idle"))
    builder.bind_attribute(
        AttributeBinding(
            scope="",
            name="setpoint",
            value=None,
            direction=AttributeDirection.IN,
        )
    )
    with pytest.raises(UnsupportedConstructError, match="no default"):
        builder.result()


def test_out_attribute_is_rejected() -> None:
    # `out` promises an output port rosetta does not generate yet; silent
    # demotion to a state variable would betray the declared intent.
    builder = RosettaBuilder("Machine")
    builder.add_state(_root())
    builder.add_state(_leaf("idle"))
    builder.bind_attribute(
        AttributeBinding(
            scope="",
            name="result",
            value=1.0,
            direction=AttributeDirection.OUT,
        )
    )
    with pytest.raises(UnsupportedConstructError, match="`out` attribute"):
        builder.result()


def test_single_channel_fan_in_is_rejected() -> None:
    """Two sources into one input -> rejected, pointing at multiplicity."""
    from sysmlc.backends.rosetta.parts import build_part_program

    model = load_model(FIXTURES_DIR / "part-fanin")
    with pytest.raises(UnsupportedConstructError, match="multiplicity"):
        build_part_program(model, "PartFanin::sys")


def test_missing_external_function_names_module() -> None:
    """Calling a calc def not in the --python module names it and the module."""
    # SM15 calls P::step; we register "phys" as the external module but
    # supply an empty name set (step absent) — should name both in the error.
    model = load_model(SM_EXAMPLES_DIR / "sm15-external")
    with pytest.raises(
        UnsupportedConstructError, match=r"step.*phys|phys.*step"
    ):
        build_program(model, "SM15::Ramp", external=("phys", frozenset()))
