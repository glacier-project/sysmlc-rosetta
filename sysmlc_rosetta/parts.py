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

``compose_exhibits`` provides the reusable N-machine composition kernel
consumed by the rig path (``build_rig_program``).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from sysmlc.backends.rosetta.builder import OUTPUT_PORT, RosettaBuilder
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


def _simple(qualified_name: str) -> str:
    """Return the last segment of a qualified name."""
    return qualified_name.split("::")[-1]


def compose_exhibits(
    model: syside.Model,
    composite_name: str,
    exhibits: tuple[tuple[str, str], ...],
    needs: PreambleNeeds,
) -> tuple[tuple[Reactor, ...], Reactor]:
    """Build N exhibited machines as children and a same-name-wired composite.

    Returns ``(child_reactor_classes, composite_reactor)``. The composite
    instantiates each machine under its instance name, cross-wires same-named
    signals among all ordered pairs (rejecting two sources into one input —
    fan-in/multiplicity), and forwards each ``current_state`` out as
    ``<instance>_current_state``.

    Args:
        model: Loaded syside model.
        composite_name: Simple name for the composite reactor.
        exhibits: ``((instance_name, behavior_qn), ...)`` in declaration order.
        needs: Shared preamble registry (caller populates before this call).
    """
    # Build the machine interface for each exhibit.
    faces = [(inst, qn, machine_interface(model, qn)) for inst, qn in exhibits]

    # Per exhibit: peer_accepts = union of every OTHER exhibit's accepted set.
    peer_accepts_map: list[frozenset[str]] = []
    for i, (_inst, _qn, _face) in enumerate(faces):
        union: set[str] = set()
        for j, (_inst2, _qn2, face2) in enumerate(faces):
            if j != i:
                union |= face2.accepted
        peer_accepts_map.append(frozenset(union))

    # Build each machine in declaration order, collecting all child reactors.
    driver = StateMachineDriver(model)
    child_reactors: list[Reactor] = []
    machine_reactors: list[Reactor] = []  # the top-level reactor per exhibit
    for (_inst, qn, _face), peer_acc in zip(
        faces, peer_accepts_map, strict=True
    ):
        result = driver.run(
            qn,
            RosettaBuilder(_simple(qn), peer_accepts=peer_acc, needs=needs),
        )
        assert isinstance(result, LfProgram)
        child_reactors.extend(result.reactors)
        machine_reactors.append(result.reactor)

    # Reactor name collision check (all child reactors + composite name).
    all_names = [r.name for r in child_reactors]
    all_names.append(composite_name)
    counts = Counter(all_names)
    duplicates = {name for name, n in counts.items() if n > 1}
    if duplicates:
        raise UnsupportedConstructError(
            f"reactor name(s) {sorted(duplicates)!r} collide across the "
            "rig; rename a machine, state, or the rig"
        )

    # Cross-wire all ordered pairs (source_i → target_j for i ≠ j).
    connections: list[Connection] = _cross_all(faces, machine_reactors)

    # Forward each exhibit's current_state as <inst>_current_state.
    for inst, _qn, _face in faces:
        connections.append(
            Connection(f"{inst}.{OUTPUT_PORT}", f"{inst}_{OUTPUT_PORT}")
        )

    composite = Reactor(
        name=composite_name,
        outputs=tuple(f"{inst}_{OUTPUT_PORT}" for inst, _qn, _face in faces),
        instantiations=tuple(
            Instantiation(inst, mach.name)
            for (inst, _qn, _face), mach in zip(
                faces, machine_reactors, strict=True
            )
        ),
        connections=tuple(connections),
    )
    return tuple(child_reactors), composite


def _cross_all(
    faces: list[tuple[str, str, MachineInterface]],
    machine_reactors: list[Reactor],
) -> list[Connection]:
    """Wire all ordered pairs of exhibits: source outputs → target inputs.

    Iterates ordered pairs (i, j) with i≠j in the order (0,1), (1,0),
    (0,2), (1,2), (2,0), ... — specifically the same order as nested
    ``for i ... for j`` loops — which for N=2 produces (0→1) then (1→0),
    matching the original ``build_rig_program`` wiring exactly.

    Rejects two sources into the same ``(target_inst, signal)`` input
    (fan-in) with the same error style as ``_route``.
    """
    # (target_inst, signal) -> source_inst already wired to it
    destinations: dict[tuple[str, str], str] = {}
    connections: list[Connection] = []
    n = len(faces)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            source_inst = faces[i][0]
            target_inst = faces[j][0]
            target_reactor = machine_reactors[j]
            source_reactor = machine_reactors[i]
            for sig in source_reactor.outputs:
                if sig == OUTPUT_PORT:
                    continue
                assert sig in target_reactor.inputs, (
                    f"{sig!r} missing from {target_reactor.name!r} inputs; "
                    "interface scan and builder disagree"
                )
                key = (target_inst, sig)
                if key in destinations:
                    raise UnsupportedConstructError(
                        f"signal {sig!r} has two sources "
                        f"({destinations[key]!r} and {source_inst!r}) "
                        f"into {target_inst!r}; single-channel "
                        "fan-in is forbidden — model it with multiplicity "
                        "(a bank into a multiport), which is deferred to a "
                        "later increment."
                    )
                destinations[key] = source_inst
                connections.append(
                    Connection(f"{source_inst}.{sig}", f"{target_inst}.{sig}")
                )
    return connections


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
        p.usage_name: machine_interface(model, p.behaviors[0][1])
        for p in g.parts
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
        if len(node.behaviors) > 1:
            raise UnsupportedConstructError(
                f"part def {node.definition_name!r} has "
                f"{len(node.behaviors)} exhibits; multi-exhibit reactor "
                "build is not yet implemented (next task after graph support)"
            )
        behavior[node.definition_name] = node.behaviors[0][1]

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
