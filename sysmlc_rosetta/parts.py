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
consumed by the rig path (``RosettaBackend.build_composition``).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from sysmlc.backends.rosetta.builder import (
    OUTPUT_PORT,
    RosettaBuilder,
    finalize,
)
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
    matching the original rig wiring exactly.

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
    external: tuple[str, frozenset[str]] | None = None,
    module_source: tuple[str, tuple[str, ...]] | None = None,
) -> LfProgram:
    """Build the composed LF program for a top-level part usage.

    Part nodes are partitioned into two groups:

    * **Single-exhibit** (``len(node.behaviors) == 1``): participate in
      port-based interface validation and signal routing, exactly as before.
    * **Multi-exhibit** (``len(node.behaviors) >= 2``): self-contained
      sub-systems built via :func:`compose_exhibits`.  They do not
      participate in ``faces`` / ``_validate_via_ports`` / ``_peer_accepts``
      / ``_route`` (their signals are internal).  A ``connect`` that
      references a multi-exhibit part is rejected with
      :class:`~sysmlc.errors.UnsupportedConstructError`.

    The ``main reactor`` instantiates ALL parts (single and multi).

    Args:
        model: Loaded syside model.
        usage_qn: Qualified name of the top-level part usage to assemble.
        target_options: Key/value pairs for the LF ``target Python { … }``
            header (e.g. ``(("fast", "true"), ("timeout", "5 sec"))``).
        external: Optional ``(module_stem, function_names)`` pair identifying
            a ``--python`` module whose top-level functions back external
            calc-def calls.  When supplied, ``module_stem`` and any matched
            function names are emitted as ``from <module> import <name>`` in
            the generated preamble.
        module_source: Optional ``(module_name, source_lines)`` pair for a
            Python module auto-extracted from SysML ``TextualRepresentation``
            annotations (as opposed to a user-supplied ``--python`` file,
            which the CLI copies to the output directory itself). When
            given, it is attached to the returned program so
            :meth:`RosettaBackend.write` writes it to disk alongside the
            ``.lf`` file.
    """
    g = part_graph(model, usage_qn)
    if not g.parts:
        raise UnsupportedConstructError(
            f"part usage {usage_qn!r} composes no parts"
        )

    single_nodes = tuple(p for p in g.parts if len(p.behaviors) == 1)
    multi_nodes = tuple(p for p in g.parts if len(p.behaviors) >= 2)
    multi_names = {p.usage_name for p in multi_nodes}

    # Reject connections that touch multi-exhibit parts (not supported yet).
    for (ia, _pa), (ib, _pb) in g.connections:
        for inst in (ia, ib):
            if inst in multi_names:
                raise UnsupportedConstructError(
                    f"connection references part {inst!r}, which is a "
                    "multi-exhibit part; connecting to a multi-exhibit part "
                    "is not supported yet"
                )

    # Port-based machinery applies to single-exhibit nodes only.
    parts = {p.usage_name: p for p in single_nodes}
    faces = {
        p.usage_name: machine_interface(model, p.behaviors[0][1])
        for p in single_nodes
    }

    _validate_via_ports(single_nodes, faces)
    _validate_connections(g, parts)

    module_name = f"{usage_qn.split('::')[-1]}_types"
    peer_accepts = _peer_accepts(g, faces)
    reactors, needs = _build_reactors(
        model,
        single_nodes,
        multi_nodes,
        peer_accepts,
        module_name=module_name,
        external=external,
    )

    main = MainReactor(
        instantiations=tuple(
            Instantiation(p.usage_name, p.definition_name) for p in g.parts
        ),
        connections=_route(g, faces),
    )
    program = LfProgram(
        reactors=reactors,
        preamble=tuple(needs.preamble_lines()),
        main=main,
        target_options=target_options,
    )
    return finalize(program, needs, external, module_source)


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
    single_nodes: tuple[PartNode, ...],
    multi_nodes: tuple[PartNode, ...],
    peer_accepts: dict[str, frozenset[str]],
    *,
    module_name: str,
    external: tuple[str, frozenset[str]] | None = None,
) -> tuple[tuple[Reactor, ...], PreambleNeeds]:
    """Build reactor classes for all part nodes, sharing one preamble.

    Single-exhibit nodes produce one inline machine reactor per distinct part
    def (with ``observe=True`` so DEBUG logging is emitted at run time).
    Multi-exhibit nodes are built via :func:`compose_exhibits`, which wires
    the exhibited machines internally.

    A part def reused by several single-exhibit parts builds once; its
    ``peer_accepts`` is the union over those usages so the shared class
    exposes every needed output.

    Args:
        model: Loaded syside model.
        single_nodes: Part nodes with exactly one exhibited behavior.
        multi_nodes: Part nodes with two or more exhibited behaviors.
        peer_accepts: Per-usage-name set of signal names accepted by peers.
        module_name: Stem of the companion ``_types`` module (set on the
            shared :class:`PreambleNeeds` before any builder pass).
        external: Optional ``(module_stem, function_names)`` pair.  When
            supplied, registered on the shared :class:`PreambleNeeds` before
            any builder pass so that matched calls land in the preamble.

    Returns:
        A tuple of ``(reactor_classes, needs)`` where ``needs`` is the shared
        preamble registry, ready for :func:`_finalize`.
    """
    needs = PreambleNeeds()
    needs.types_module = module_name
    if external is not None:
        needs.register_external(module=external[0], names=external[1])
    seen: set[str] = set()
    reactors: list[Reactor] = []

    # --- Single-exhibit nodes: one reactor per distinct def (observe=True) ---
    union: dict[str, set[str]] = defaultdict(set)
    behavior: dict[str, str] = {}
    for node in single_nodes:
        union[node.definition_name] |= peer_accepts[node.usage_name]
        behavior[node.definition_name] = node.behaviors[0][1]

    driver = StateMachineDriver(model)
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

    # --- Multi-exhibit nodes: build via compose_exhibits (once per def) ---
    built_multi: set[str] = set()
    for node in multi_nodes:
        if node.definition_name in built_multi:
            continue
        built_multi.add(node.definition_name)
        children, composite = compose_exhibits(
            model, node.definition_name, node.behaviors, needs
        )
        for reactor in (*children, composite):
            if reactor.name in seen:
                raise UnsupportedConstructError(
                    f"reactor name {reactor.name!r} collides across parts; "
                    "rename a part def, state, or machine"
                )
            seen.add(reactor.name)
            reactors.append(reactor)

    return tuple(reactors), needs


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