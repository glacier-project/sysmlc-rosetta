"""Assemble a SysML part system into one LF program (port-based routing).

Walks the part graph (``sysmlc.semantics.parts.graph``), builds one reactor
class per part definition (reusing the state-machine codegen, named after the
part def), and emits an explicit ``main reactor`` that instantiates each part
and wires its connected ports.

Connection routing is **port-based** (spec rosetta-parts-design.md §5.3): for
``connect a.pa to b.pb`` a signal crosses only if it is *sent via* ``pa`` on
one end and *accepted via* ``pb`` on the other (the port-aware interface
maps). This generalizes the rig's name-based ``_cross`` to N parts while
honouring the SysML ports, and validates ports strictly.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from sysmlc.backends.rosetta.builder import RosettaBuilder
from sysmlc.backends.rosetta.codegen import PreambleNeeds
from sysmlc.backends.rosetta.program import (
    Connection,
    Instantiation,
    LfProgram,
    MainReactor,
    Reactor,
)
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.parts.graph import PartGraph, PartNode, part_graph
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.semantics.statemachine.interface import (
    MachineInterface,
    machine_interface,
)

if TYPE_CHECKING:
    import syside

logger = logging.getLogger(__name__)


def build_part_program(
    model: syside.Model,
    usage_qn: str,
    *,
    target_options: tuple[tuple[str, str], ...] = (),
) -> LfProgram:
    """Build the composed LF program for a top-level part usage."""
    g = part_graph(model, usage_qn)
    if not g.parts:
        raise UnsupportedConstructError(
            f"part usage {usage_qn!r} composes no parts"
        )
    parts = {p.usage_name: p for p in g.parts}
    faces = {
        p.usage_name: machine_interface(model, p.behavior_qn) for p in g.parts
    }

    _validate_via_ports(g.parts, faces)
    _validate_connections(g, parts)

    peer_accepts = _peer_accepts(g, faces)
    reactors, preamble = _build_reactors(model, g.parts, peer_accepts)

    main = MainReactor(
        instantiations=tuple(
            Instantiation(p.usage_name, p.definition_name) for p in g.parts
        ),
        connections=_route(g, faces),
    )
    return LfProgram(
        reactors=reactors,
        preamble=preamble,
        main=main,
        target_options=target_options,
    )


def _validate_via_ports(
    nodes: tuple[PartNode, ...],
    faces: dict[str, MachineInterface],
) -> None:
    """Reject a behavior whose ``send/accept via P`` port is undeclared."""
    for node in nodes:
        face = faces[node.usage_name]
        declared = set(node.ports)
        used = (set(face.sent_via) | set(face.accepted_via)) - {None}
        for port in sorted(p for p in used if p is not None):
            if port not in declared:
                raise UnsupportedConstructError(
                    f"part def {node.definition_name!r} sends/accepts via "
                    f"port {port!r}, which it does not declare; declared "
                    f"ports: {sorted(declared)!r}"
                )


def _validate_connections(g: PartGraph, parts: dict[str, PartNode]) -> None:
    """Reject a ``connect`` naming an unknown part or undeclared port."""
    for (ia, pa), (ib, pb) in g.connections:
        for inst, port in ((ia, pa), (ib, pb)):
            node = parts.get(inst)
            if node is None:
                raise UnsupportedConstructError(
                    f"connection references part {inst!r}, which is not a "
                    f"part of {g.name!r}"
                )
            if port not in node.ports:
                raise UnsupportedConstructError(
                    f"connection references port {port!r}, which is not a "
                    f"declared port of part def {node.definition_name!r}"
                )


def _peer_accepts(
    g: PartGraph, faces: dict[str, MachineInterface]
) -> dict[str, frozenset[str]]:
    """Per part: signals a connected peer accepts via the matching port.

    Drives which of a part's sends become output ports — scoped to the
    ports actually connected, not global name matching.
    """
    acc: dict[str, set[str]] = defaultdict(set)
    for (ia, pa), (ib, pb) in g.connections:
        acc[ia] |= faces[ib].accepted_via.get(pb, frozenset())
        acc[ib] |= faces[ia].accepted_via.get(pa, frozenset())
    return {p.usage_name: frozenset(acc[p.usage_name]) for p in g.parts}


def _build_reactors(
    model: syside.Model,
    nodes: tuple[PartNode, ...],
    peer_accepts: dict[str, frozenset[str]],
) -> tuple[tuple[Reactor, ...], tuple[str, ...]]:
    """Build one reactor class per distinct part def, sharing a preamble.

    A part def reused by several parts builds once; its ``peer_accepts`` is the
    union over those usages so the shared class exposes every needed output.
    Returns the reactor classes and the shared preamble lines.
    """
    union: dict[str, set[str]] = defaultdict(set)
    behavior: dict[str, str] = {}
    for node in nodes:
        union[node.definition_name] |= peer_accepts[node.usage_name]
        behavior[node.definition_name] = node.behavior_qn

    needs = PreambleNeeds()
    driver = StateMachineDriver(model)
    reactors: list[Reactor] = []
    seen: set[str] = set()
    for def_name, behavior_qn in behavior.items():
        result = driver.run(
            behavior_qn,
            RosettaBuilder(
                def_name,
                peer_accepts=frozenset(union[def_name]),
                needs=needs,
                observe=True,
            ),
        )
        assert isinstance(result, LfProgram)
        for reactor in result.reactors:
            if reactor.name in seen:
                raise UnsupportedConstructError(
                    f"reactor name {reactor.name!r} collides across parts; "
                    "rename a part def, state, or machine"
                )
            seen.add(reactor.name)
            reactors.append(reactor)
    return tuple(reactors), tuple(needs.preamble_lines())


def _route(
    g: PartGraph, faces: dict[str, MachineInterface]
) -> tuple[Connection, ...]:
    """Wire each connection's matched ports, both directions.

    A signal crosses ``connect a.pa to b.pb`` only when one end sends it via
    its port and the other accepts it via its port. A signal that travels both
    ways over a single connection (same name) is rejected, as in the rig.
    """
    connections: list[Connection] = []
    # (target instance, signal) -> the source instance already wired to it,
    # so a second source into the same single-channel input is rejected.
    destinations: dict[tuple[str, str], str] = {}

    def wire(source_inst: str, target_inst: str, sig: str) -> None:
        key = (target_inst, sig)
        if key in destinations:
            raise UnsupportedConstructError(
                f"signal {sig!r} has two sources ({destinations[key]!r} and "
                f"{source_inst!r}) into {target_inst!r}; single-channel "
                "fan-in is forbidden — model it with multiplicity (a bank "
                "into a multiport), which is deferred to a later increment."
            )
        destinations[key] = source_inst
        connections.append(
            Connection(f"{source_inst}.{sig}", f"{target_inst}.{sig}")
        )

    for (ia, pa), (ib, pb) in g.connections:
        fa, fb = faces[ia], faces[ib]
        a_to_b = fa.sent_via.get(pa, frozenset()) & fb.accepted_via.get(
            pb, frozenset()
        )
        b_to_a = fb.sent_via.get(pb, frozenset()) & fa.accepted_via.get(
            pa, frozenset()
        )
        both = a_to_b & b_to_a
        if both:
            raise UnsupportedConstructError(
                f"signal(s) {sorted(both)!r} travel both ways over the "
                f"connection {ia}.{pa} <-> {ib}.{pb}; bidirectional "
                "same-name signals are not supported"
            )
        for sig in sorted(a_to_b):
            wire(ia, ib, sig)
        for sig in sorted(b_to_a):
            wire(ib, ia, sig)
        _warn_unwired(ia, pa, fa, fb, pb)
        _warn_unwired(ib, pb, fb, fa, pa)
    return tuple(connections)


def _warn_unwired(
    inst: str,
    port: str,
    face: MachineInterface,
    peer: MachineInterface,
    peer_port: str,
) -> None:
    """Warn when a part accepts a signal via a port the peer never sends."""
    accepted = face.accepted_via.get(port, frozenset())
    delivered = peer.sent_via.get(peer_port, frozenset())
    for sig in sorted(accepted - delivered):
        logger.warning(
            "part %r accepts %r via %r but its peer never sends it; the "
            "input port stays unwired",
            inst,
            sig,
            port,
        )
