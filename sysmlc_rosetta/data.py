"""Transport references to machine data through existing entry dependencies."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from sysmlc.errors import UnsupportedConstructError

from sysmlc_rosetta.lifecycle import ENTRY_ORDER_PORT, ENTRY_TRIGGERS
from sysmlc_rosetta.program import Reaction
from sysmlc_rosetta.statements import (
    assigns,
    insert_after_assignments,
    nest,
    stored,
)

if TYPE_CHECKING:
    from sysmlc_rosetta.program import Instantiation, Reactor
    from sysmlc_rosetta.targets.base import LfTarget

CHANGED_PORT = "_data_changed"


def share_machine_data(
    reactors: list[Reactor],
    target: LfTarget,
    ctype: str | None,
    attributes: tuple[str, ...],
    contexts: frozenset[str],
    *,
    has_when: bool,
) -> list[Reactor]:
    """Carry root storage to nested scopes and report their assignments.

    Args:
        reactors: Child-first reactor definitions, with the machine last.
        target: The target language used by the reactor bodies.
        ctype: The native context type, or None for Python.
        attributes: Root attributes whose storage the context references.
        contexts: Reactors requiring the root context or forwarding it.
        has_when: Whether descendant writes must notify root observations.

    Returns:
        Reactors with context transport and assignment notifications.

    Raises:
        UnsupportedConstructError: If a source name conflicts with data plumbing.
    """
    changed: set[str] = set()
    return [
        _share_reactor(
            reactor,
            target,
            ctype,
            attributes,
            contexts,
            changed,
            root=reactor is reactors[-1],
            has_when=has_when,
        )
        for reactor in reactors
    ]


def _share_reactor(
    reactor: Reactor,
    target: LfTarget,
    ctype: str | None,
    attributes: tuple[str, ...],
    contexts: frozenset[str],
    changed: set[str],
    *,
    root: bool,
    has_when: bool,
) -> Reactor:
    """Wire one scope, placing notifications after its lifecycle cleanup."""
    names = frozenset(attributes)
    child = reactor.name in contexts
    identifiers = {
        *reactor.inputs,
        *reactor.outputs,
        *(p.name for p in reactor.parameters),
        *(v.name for v in reactor.state_vars),
        *(a.name for a in reactor.actions),
        *(mode.name for mode in reactor.modes),
        *(i.name for i in reactor.instantiations),
        *(i.name for mode in reactor.modes for i in mode.instantiations),
        *(a.name for mode in reactor.modes for a in mode.actions),
        *(t.name for mode in reactor.modes for t in mode.timers),
    }
    if child and "_data" in identifiers:
        raise UnsupportedConstructError(
            "name '_data' conflicts with nested machine data storage"
        )
    value = (
        target.context_value(ctype, attributes) if root else target.var("_data")
    )
    notified = False
    notifications: list[Reaction] = []

    def patched(reaction: Reaction) -> Reaction:
        nonlocal notified
        body = reaction.body
        effects = reaction.effects
        if child and set(reaction.triggers) == set(ENTRY_TRIGGERS):
            body = stored(
                (
                    nest(
                        target.is_present(ENTRY_ORDER_PORT),
                        (target.context_binding(ENTRY_ORDER_PORT),),
                    ),
                    *body,
                )
            )
        if child and has_when and assigns(body) & names:
            body, _ = insert_after_assignments(
                body,
                names,
                target.set_port(CHANGED_PORT, target.boolean(True)),
            )
            effects = (*effects, CHANGED_PORT)
            notified = True
        return replace(reaction, body=stored(body), effects=effects)

    def forwarded(
        reactions: tuple[Reaction, ...],
        instances: tuple[Instantiation, ...],
    ) -> tuple[Reaction, ...]:
        nonlocal notified
        receiving = tuple(i.name for i in instances if i.reactor in contexts)
        output = []
        for reaction in reactions:
            reaction = patched(reaction)
            ports = tuple(
                f"{name}.{ENTRY_ORDER_PORT}"
                for name in receiving
                if f"{name}.{ENTRY_ORDER_PORT}" in reaction.effects
            )
            output.append(
                replace(
                    reaction,
                    body=stored(
                        (
                            *reaction.body,
                            *(target.set_port(port, value) for port in ports),
                        )
                    ),
                )
            )
        for inst in instances:
            if inst.reactor not in changed:
                continue
            statement = (
                target.schedule("_change_act")
                if root
                else target.set_port(CHANGED_PORT, target.boolean(True))
            )
            effect = "_change_act" if root else CHANGED_PORT
            notification = Reaction(
                (f"{inst.name}.{CHANGED_PORT}",),
                (effect,),
                stored((statement,)),
            )
            if target.mode_local_children:
                output.append(notification)
            else:
                notifications.append(notification)
            notified = True
        return tuple(output)

    reactions = forwarded(reactor.reactions, reactor.instantiations)
    modes = tuple(
        replace(mode, reactions=forwarded(mode.reactions, mode.instantiations))
        for mode in reactor.modes
    )
    trailing = (
        *(patched(r) for r in reactor.trailing_reactions),
        *notifications,
    )
    if child and notified:
        if CHANGED_PORT in identifiers:
            raise UnsupportedConstructError(
                f"name {CHANGED_PORT!r} conflicts with nested data notifications"
            )
        changed.add(reactor.name)
    ports = reactor.port_types
    if child and ctype is not None:
        ports = (*ports, (ENTRY_ORDER_PORT, ctype))
    return replace(
        reactor,
        state_vars=(
            (*reactor.state_vars, target.context_state(ctype))
            if child
            else reactor.state_vars
        ),
        outputs=(
            (*reactor.outputs, CHANGED_PORT)
            if child and notified
            else reactor.outputs
        ),
        port_types=ports,
        reactions=reactions,
        modes=modes,
        trailing_reactions=trailing,
    )
