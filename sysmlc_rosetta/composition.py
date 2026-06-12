"""Compose a rig's two state machines into one LF program."""

from __future__ import annotations

import logging

import syside

from sysmlc.backends.rosetta.builder import OUTPUT_PORT, RosettaBuilder
from sysmlc.backends.rosetta.codegen import PreambleNeeds
from sysmlc.backends.rosetta.program import (
    Connection,
    Instantiation,
    LfProgram,
    Reactor,
)
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.semantics.statemachine.interface import machine_interface
from sysmlc.sysml.queries import exhibited_state_defs, resolve

logger = logging.getLogger(__name__)


def build_rig_program(model: syside.Model, rig_qn: str) -> LfProgram:
    """Build the composed LF program for a testbench rig.

    Each exhibited machine builds once with ``peer_accepts`` set to the
    other's accepted signals and a shared preamble registry; a bench
    reactor (named after the rig) instantiates both under their usage
    names, wires same-named signals in both directions, and forwards
    each machine's ``current_state`` as ``<usage>_current_state``.
    """
    rig = resolve(model, syside.PartDefinition, rig_qn)
    (usage_a, def_a), (usage_b, def_b) = exhibited_state_defs(model, rig)
    qn_a = str(def_a.qualified_name)
    qn_b = str(def_b.qualified_name)
    if qn_a == qn_b:
        raise UnsupportedConstructError(
            f"rig {rig.name!r} exhibits {qn_a!r} twice; a rig composes "
            "two distinct state defs"
        )
    face_a = machine_interface(model, qn_a)
    face_b = machine_interface(model, qn_b)
    both = (face_a.sent & face_b.accepted) & (face_b.sent & face_a.accepted)
    if both:
        raise UnsupportedConstructError(
            f"signal(s) {sorted(both)!r} are sent by both machines; "
            "bidirectional same-name signals are not supported"
        )
    for usage, face, peer in (
        (usage_a, face_a, face_b),
        (usage_b, face_b, face_a),
    ):
        for sig in sorted(face.accepted - peer.sent - face.sent):
            logger.warning(
                "machine %r accepts %r but its peer never sends it; the "
                "input port stays unwired",
                usage,
                sig,
            )
    needs = PreambleNeeds()
    driver = StateMachineDriver(model)
    prog_a = driver.run(
        qn_a,
        RosettaBuilder(
            _simple(qn_a), peer_accepts=face_b.accepted, needs=needs
        ),
    )
    prog_b = driver.run(
        qn_b,
        RosettaBuilder(
            _simple(qn_b), peer_accepts=face_a.accepted, needs=needs
        ),
    )
    assert isinstance(prog_a, LfProgram)
    assert isinstance(prog_b, LfProgram)
    rig_name = _simple(rig_qn)
    names = [r.name for r in (*prog_a.reactors, *prog_b.reactors)]
    names.append(rig_name)
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise UnsupportedConstructError(
            f"reactor name(s) {sorted(duplicates)!r} collide across the "
            "rig; rename a machine, state, or the rig"
        )
    bench = Reactor(
        name=rig_name,
        outputs=(
            f"{usage_a}_{OUTPUT_PORT}",
            f"{usage_b}_{OUTPUT_PORT}",
        ),
        instantiations=(
            Instantiation(usage_a, prog_a.reactor.name),
            Instantiation(usage_b, prog_b.reactor.name),
        ),
        connections=(
            *_cross(prog_a.reactor, usage_a, prog_b.reactor, usage_b),
            *_cross(prog_b.reactor, usage_b, prog_a.reactor, usage_a),
            Connection(f"{usage_a}.{OUTPUT_PORT}", f"{usage_a}_{OUTPUT_PORT}"),
            Connection(f"{usage_b}.{OUTPUT_PORT}", f"{usage_b}_{OUTPUT_PORT}"),
        ),
    )
    return LfProgram(
        reactors=(*prog_a.reactors, *prog_b.reactors, bench),
        preamble=tuple(needs.preamble_lines()),
    )


def _simple(qualified_name: str) -> str:
    """Return the last segment of a qualified name."""
    return qualified_name.split("::")[-1]


def _cross(
    source: Reactor,
    source_inst: str,
    target: Reactor,
    target_inst: str,
) -> list[Connection]:
    """Wire the source machine's signal ports into the target's inputs.

    By construction every machine output (except ``current_state``) is a
    signal its peer accepts, and the both-send rejection guarantees the
    matching input exists.
    """
    out: list[Connection] = []
    for sig in source.outputs:
        if sig == OUTPUT_PORT:
            continue
        assert sig in target.inputs, sig
        out.append(Connection(f"{source_inst}.{sig}", f"{target_inst}.{sig}"))
    return out
