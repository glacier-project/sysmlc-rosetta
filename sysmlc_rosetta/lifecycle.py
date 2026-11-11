from __future__ import annotations

from typing import TYPE_CHECKING

from sysmlc_rosetta.program import Reaction
from sysmlc_rosetta.statements import If, nest, stored, writes

if TYPE_CHECKING:
    from sysmlc_rosetta.statements import Body, Node
    from sysmlc_rosetta.targets.base import LfTarget

OUTPUT_PORT = "current_state"
COMPLETED_PORT = "completed"
ENTRY_ORDER_PORT = "_entry_order"
BUSY_PORT = "_busy"
SETTLED_PORT = "_settled"
EXIT_REQUEST_PORT = "_exit"
EXIT_ACK_PORT = "_exited"
ACTIVE_VAR = "_active"
EXIT_PENDING_VAR = "_exit_requested"


def when_due(target: LfTarget, due: str, body: Body) -> Body:
    """Run a time trigger only for the callback its activation armed.

    The LF 0.13 runtime retains old events on active-mode reset, contrary
    to its documented reset semantics. Entry records the current due time;
    the first callback at it consumes the record before guard or retention.
    """
    return (nest(target.due_reached(due), (target.clear_due(due), *body)),)


def declining(
    target: LfTarget,
    pending: str | None,
    held: list[tuple[int, str]],
    body: Body,
) -> tuple[Body, tuple[str, ...]]:
    """Decline a retained signal if ``body`` ends its source activation.

    An earlier parallel region's gate waits for the outcome of a held
    occurrence; ending the activation without accepting it reports it
    declined on the signal's consumption flag.

    Args:
        target: The target language.
        pending: The source state's pending slot, or None.
        held: Retained signal keys and their consumption flags.
        body: The statements that may end the source activation.

    Returns:
        The wrapped statements and the consumption flags they may set.
    """
    if pending is None or not held:
        return body, ()
    flags = tuple(
        nest(target.held_is(key), (target.consume(flag, target.declined()),))
        for key, flag in held
    )
    wrapped = (
        target.bind_held(pending),
        *body,
        nest(target.pending_closed(pending), flags),
    )
    return wrapped, tuple(flag for _, flag in held)


def busy_condition(target: LfTarget, state: str, insts: tuple[str, ...]) -> str:
    """The condition that a source state's subtree is settling."""
    return target.any_of(
        (
            target.busy_any(state, insts),
            *(target.is_present(f"{inst}.{BUSY_PORT}") for inst in insts),
        )
    )


def retain(
    target: LfTarget,
    body: Body,
    key: int,
    payload: str,
    state: str,
    insts: tuple[str, ...],
    message: str,
    consumed: str | None = None,
    arrival: str | None = None,
    signal: str | None = None,
) -> Body:
    """Retain a trigger while the source state's subtree is settling.

    A child whose busy port is present at this tag selected its
    transition in the same reaction cycle; the busy marks hold the
    children announced busy earlier that have not settled yet. The
    retained entry keeps the trigger key, captured payload and arrival
    tag. At most one occurrence is retained: a second arrival while one
    is held raises a runtime error rather than reorder or drop either.
    A retained signal marks its consumption flag as held: enclosing
    candidates still treat the occurrence as unconsumed, and a later
    region must not accept it meanwhile.

    Args:
        target: The target language.
        body: The immediate dispatch statements.
        key: The trigger's identity within its source state.
        payload: Expression for the payload to retain.
        state: The resolved simple name of the source state.
        insts: The source state's child reactor instances.
        message: The diagnostic for overlapping retained occurrences.
        consumed: The signal's consumption-flag output, or None.
        arrival: Expression for the occurrence's arrival tag; the current
            tag when None.
        signal: The channel whose payload is retained, or None.
    """
    pending = f"_{state}_pending"
    holding: list[Node] = [
        nest(target.pending_holding(pending), (target.fail(message),)),
        target.pending_hold(pending, key, payload, signal),
    ]
    if consumed:
        holding.append(target.consume(consumed, target.held()))
    return (
        target.bind_tag(target.arrival() if arrival is None else arrival),
        If(
            ((busy_condition(target, state, insts), tuple(holding)),),
            orelse=body,
        ),
    )


def overtaking(
    target: LfTarget, state: str, insts: tuple[str, ...], message: str
) -> tuple[Body, Body]:
    """Reject a later occurrence accepted below a retaining state.

    Args:
        target: The target language.
        state: The resolved simple name of the source state.
        insts: The source state's child reactor instances.
        message: The diagnostic for a later accepted occurrence.

    Returns:
        The statements binding the latest arrival tag the children report
        busy with at this tag, and the check failing when it is later than
        the retained occurrence's arrival.
    """
    pending = f"_{state}_pending"
    return target.bind_newest(insts), (
        nest(
            target.pending_later_than(pending, target.newest()),
            (target.fail(message),),
        ),
    )


def exit_handler(
    target: LfTarget,
    exits: list[tuple[str, Body, tuple[str, ...]]],
    sent: list[str],
    ports: tuple[str, ...],
) -> Reaction:
    """Exit the active state of a scope on its parent's request.

    An active leaf runs its exit and acknowledges at once; an active
    composite/parallel state forwards the request to its first child
    and finishes in its drain chain. A scope that is already finalized
    (or not yet active) acknowledges without running an exit. The active
    slot holds a mode while active, the cleanup mark during selected
    cleanup, and nothing once exited. A request arriving during cleanup
    waits for its continuation to acknowledge rather than restarting its
    exits.

    Args:
        target: The target language.
        exits: Per mode, its forced-exit statements and their effects.
        sent: Signals sent in this scope.
        ports: Peer-accepted signals sent in this scope.
    """
    effects: dict[str, None] = {}
    branches: list[tuple[str, Body]] = []
    for mode, lines, mode_effects in exits:
        branches.append((target.active_is(mode), lines))
        effects.update(dict.fromkeys(mode_effects))
    branches.append(
        (
            target.active_is_none(),
            (
                target.set_port(EXIT_ACK_PORT, target.boolean(True)),
                target.set_flag(EXIT_PENDING_VAR, False),
            ),
        )
    )
    effects[EXIT_ACK_PORT] = None
    body = (target.set_flag(EXIT_PENDING_VAR, True), If(tuple(branches)))
    return make_reaction(
        (EXIT_REQUEST_PORT,),
        tuple(effects),
        body,
        sent,
        ports,
    )


def lifecycle_reaction(
    target: LfTarget,
    state: str,
    insts: tuple[str, ...],
    retained: list[tuple[int, Body, tuple[str, ...]]],
    consumed: list[tuple[int, tuple[str, ...]]],
    sent: list[str],
    held: list[tuple[int, str]],
    overtaking: tuple[Body, Body],
    *,
    report_upward: bool,
    ports: tuple[str, ...],
) -> Reaction:
    """Track a composite/parallel state's children until they settle.

    A child reports busy when it selects a transition and settled once
    its target entry, nested entries, eventless closure and natural
    region finalization are complete. Busy carries the arrival tag of
    the occurrence that selected the transition, or the continuation for
    an eventless one. When no child is busy, the retained trigger
    dispatches with its captured payload and arrival tag; an all-false
    trigger is retired, not retried. A retained signal that a
    descendant consumed under the same arrival tag is dropped, because
    that descendant accepted the same occurrence. A descendant
    accepting a later occurrence while one is retained raises a runtime
    error rather than reorder the two. A reporting scope forwards busy,
    with the latest arrival tag, and settled upward.

    Args:
        target: The target language.
        state: The resolved simple name of the source state.
        insts: The source state's child reactor instances.
        retained: Per trigger, its key, payload-aware dispatch statements
            and their effects.
        consumed: Per signal trigger, its key and the descendant
            consumption ports it yields to.
        sent: Signals sent in this scope.
        held: Retained signal keys and their consumption flags; a
            retained signal whose guards are all false is declined.
        overtaking: The statements binding the latest arrival, and the
            check rejecting overtaking (see :func:`overtaking`).
        report_upward: Whether this scope reports busy and settled upward.
        ports: Peer-accepted signals sent in this scope.
    """
    pending = f"_{state}_pending"
    body: list[Node] = [
        If(
            (
                (
                    target.is_present(f"{inst}.{BUSY_PORT}"),
                    (target.busy_add(state, inst),),
                ),
                (
                    target.is_present(f"{inst}.{SETTLED_PORT}"),
                    (target.busy_discard(state, inst),),
                ),
            )
        )
        for inst in insts
    ]
    bind_newest, reject_overtaking = overtaking
    body += [*bind_newest, *(reject_overtaking if retained else ())]
    for key, sources in consumed:
        cond = target.any_of(
            [
                target.consumed_at(src, target.pending_tag(pending))
                for src in sources
            ]
        )
        body.append(
            nest(
                target.all_of(
                    (target.pending_key_is(pending, key), target.grouped(cond))
                ),
                (target.pending_open(pending),),
            )
        )
    effects: dict[str, None] = {}
    if retained:
        flags = dict(held)
        branches: list[tuple[str, Body]] = []
        for key, lines, targets in retained:
            branch = lines
            if key in flags:
                branch = (
                    *branch,
                    nest(
                        target.pending_not_closed(pending),
                        (target.consume(flags[key], target.declined()),),
                    ),
                )
            branches.append((target.taken_key_is(key), branch))
            effects.update(dict.fromkeys(targets))
        body.append(
            nest(
                target.all_of(
                    (
                        target.busy_none(state, insts),
                        target.pending_holding(pending),
                    )
                ),
                (
                    *target.pending_take(pending),
                    target.pending_open(pending),
                    If(tuple(branches)),
                ),
            )
        )
    if report_upward:
        body.append(
            If(
                (
                    (
                        target.busy_any(state, insts),
                        (target.report_busy(target.newest()),),
                    ),
                    (
                        target.pending_not_closed(pending),
                        (target.set_port(SETTLED_PORT, target.boolean(True)),),
                    ),
                )
            )
        )
        effects.update(dict.fromkeys((BUSY_PORT, SETTLED_PORT)))
    return make_reaction(
        (
            *(f"{inst}.{BUSY_PORT}" for inst in insts),
            *(f"{inst}.{SETTLED_PORT}" for inst in insts),
        ),
        tuple(effects),
        tuple(body),
        sent,
        ports,
        sources=tuple(
            dict.fromkeys(src for _, srcs in consumed for src in srcs)
        ),
    )


def make_reaction(
    triggers: tuple[str, ...],
    effects: tuple[str, ...],
    body: Body,
    sent: list[str],
    ports: tuple[str, ...] = (),
    *,
    sources: tuple[str, ...] = (),
) -> Reaction:
    """Build a reaction, declaring scheduled actions and set ports.

    LF passes only the names listed in the effects clause into the
    reaction, so every reactor-level logical action the body schedules and
    every output port the body sets must be declared as an effect. The
    statements report what they write.
    """
    written = writes(body)
    scheduled = (f"{sig}_act" for sig in sent if f"{sig}_act" in written)
    set_ports = (sig for sig in ports if sig in written)
    all_effects = tuple(dict.fromkeys((*effects, *scheduled, *set_ports)))
    return Reaction(triggers, all_effects, stored(body), sources)
