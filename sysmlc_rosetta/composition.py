"""Compose a rig's exhibited state machines into one LF program."""

from __future__ import annotations

import logging

import syside

from sysmlc.backends.rosetta.codegen import PreambleNeeds
from sysmlc.backends.rosetta.parts import compose_exhibits
from sysmlc.backends.rosetta.program import LfProgram
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.statemachine.interface import machine_interface
from sysmlc.sysml.queries import exhibited_state_defs, resolve

logger = logging.getLogger(__name__)


def build_rig_program(
    model: syside.Model,
    rig_qn: str,
    *,
    external: tuple[str, frozenset[str]] | None = None,
) -> LfProgram:
    """Build the composed LF program for a testbench rig.

    Each exhibited machine builds once with ``peer_accepts`` set to the
    other's accepted signals and a shared preamble registry; a bench
    reactor (named after the rig) instantiates both under their usage
    names, wires same-named signals in both directions, and forwards
    each machine's ``current_state`` as ``<usage>_current_state``.

    Delegates composition to
    :func:`~sysmlc.backends.rosetta.parts.compose_exhibits`.
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
    # These interface-scan sets are BY CONSTRUCTION the same facts the
    # builder consumes: both run the same StateMachineDriver over the same
    # model, so the builder's ported outputs are exactly sent ∩ peer.accepted.
    # The warning therefore agrees with the actual wiring produced below.
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
    if external is not None:
        needs.register_external(module=external[0], names=external[1])
    children, composite = compose_exhibits(
        model,
        _simple(rig_qn),
        ((usage_a, qn_a), (usage_b, qn_b)),
        needs,
    )
    return LfProgram(
        reactors=(*children, composite),
        preamble=tuple(needs.preamble_lines()),
    )


def _simple(qualified_name: str) -> str:
    """Return the last segment of a qualified name."""
    return qualified_name.split("::")[-1]
