"""Transport references to machine data through existing entry dependencies."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from sysmlc_rosetta.lifecycle import (
    COMPLETED_PORT,
    ENTRY_ORDER_PORT,
    ENTRY_TRIGGERS,
)
from sysmlc_rosetta.program import Reaction
from sysmlc_rosetta.statements import (
    insert_after_assignments,
    nest,
    stored,
)

if TYPE_CHECKING:
    from sysmlc_rosetta.program import Instantiation, Reactor
    from sysmlc_rosetta.targets.base import LfTarget


def share_machine_data(
    reactors: list[Reactor],
    target: LfTarget,
    ctype: str | None,
    attributes: tuple[str, ...],
    contexts: dict[str, tuple[str, str]],
    *,
    observed_attributes: frozenset[str],
) -> list[Reactor]:
    """Carry root storage to nested scopes and report their assignments.

    Args:
        reactors: Child-first reactor definitions, with the machine last.
        target: The target language used by the reactor bodies.
        ctype: The native context type, or None for Python.
        attributes: Storage slots whose references the context transports.
        contexts: Reactor classes mapped to their context slot and change
            output identifiers.
        observed_attributes: Slots whose writes notify root observations;
            empty when the machine has no change observations.

    Returns:
        Reactors with context transport and assignment notifications.
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
            observed_attributes=observed_attributes,
        )
        for reactor in reactors
    ]


def _share_reactor(
    reactor: Reactor,
    target: LfTarget,
    ctype: str | None,
    attributes: tuple[str, ...],
    contexts: dict[str, tuple[str, str]],
    changed: set[str],
    *,
    root: bool,
    observed_attributes: frozenset[str],
) -> Reactor:
    """Wire one scope, placing notifications after its lifecycle cleanup."""
    child = reactor.name in contexts
    if not root and not child:
        return reactor
    context_name, changed_port = ("", "") if root else contexts[reactor.name]
    value = (
        target.context_value(ctype, attributes)
        if root
        else target.var(context_name)
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
                        (
                            target.context_binding(
                                context_name, ENTRY_ORDER_PORT
                            ),
                        ),
                    ),
                    *body,
                )
            )
        if child and observed_attributes:
            body, assigned = insert_after_assignments(
                body,
                observed_attributes,
                target.set_port(changed_port, target.boolean(True)),
            )
            if assigned:
                effects = (*effects, changed_port)
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
        if root:
            missing = tuple(
                f"{name}.{ENTRY_ORDER_PORT}"
                for name in receiving
                if not any(
                    f"{name}.{ENTRY_ORDER_PORT}" in reaction.effects
                    for reaction in reactions
                )
            )
            if missing:
                output.insert(
                    0,
                    Reaction(
                        ("startup",),
                        missing,
                        stored(
                            tuple(target.set_port(p, value) for p in missing)
                        ),
                    ),
                )
        for inst in instances:
            if inst.reactor not in changed:
                continue
            statement = (
                target.schedule("_change_act")
                if root
                else target.set_port(changed_port, target.boolean(True))
            )
            effect = "_change_act" if root else changed_port
            notification = Reaction(
                (f"{inst.name}.{contexts[inst.reactor][1]}",),
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
        changed.add(reactor.name)
    ports = reactor.port_types
    if child and ctype is not None:
        ports = (*ports, (ENTRY_ORDER_PORT, ctype))
    if child and notified:
        ports = (*ports, *target.port_types_as((changed_port,), COMPLETED_PORT))
    return replace(
        reactor,
        state_vars=(
            (*reactor.state_vars, target.context_state(context_name, ctype))
            if child
            else reactor.state_vars
        ),
        outputs=(
            (*reactor.outputs, changed_port)
            if child and notified
            else reactor.outputs
        ),
        port_types=ports,
        reactions=reactions,
        modes=modes,
        trailing_reactions=trailing,
    )
