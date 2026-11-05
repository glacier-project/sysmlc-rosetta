from __future__ import annotations

import json

from sysmlc_rosetta.program import Reaction

ENTRY_ORDER_PORT = "_entry_order"
BUSY_PORT = "_busy"
SETTLED_PORT = "_settled"
EXIT_REQUEST_PORT = "_exit"
EXIT_ACK_PORT = "_exited"
ACTIVE_VAR = "_active"
EXIT_PENDING_VAR = "_exit_requested"
# Eventless continuations sort before arrival tags.
_ARRIVAL = "(lf.time.logical_elapsed(), lf.tag().microstep)"
_CONTINUATION = "()"
_PY_INDENT = "    "


def when_due(due: str, body: list[str]) -> list[str]:
    """Run a time trigger only for the callback its activation armed.

    The LF 0.13 runtime retains old events on active-mode reset, contrary
    to its documented reset semantics. Entry records the current due time;
    the first callback at it consumes the record before guard or retention.
    """
    return [
        f"if lf.time.logical_elapsed() == self.{due}:",
        f"{_PY_INDENT}self.{due} = None",
        *(f"{_PY_INDENT}{line}" for line in body),
    ]


def report_busy(occurrence: str) -> str:
    """Report busy, keeping the latest arrival tag reported at this tag."""
    return (
        f"{BUSY_PORT}.set(max({BUSY_PORT}.value, {occurrence}) "
        f"if {BUSY_PORT}.is_present else {occurrence})"
    )


def declining(
    pending: str | None,
    held: list[tuple[int, str]],
    body: list[str],
) -> tuple[list[str], tuple[str, ...]]:
    """Decline a retained signal if ``body`` ends its source activation.

    An earlier parallel region's gate waits for the outcome of a held
    occurrence; ending the activation without accepting it reports
    ``False`` on the signal's consumption flag.

    Args:
        pending: The source state's pending-trigger variable, or None.
        held: Retained signal keys and their consumption flags.
        body: The lines that may end the source activation.

    Returns:
        The wrapped lines and the consumption flags they may set.
    """
    if pending is None or not held:
        return body, ()
    lines = [
        f"_held = self.{pending}[0] if self.{pending} else None",
        *body,
        f"if self.{pending} is None:",
    ]
    for key, flag in held:
        lines += [
            f"{_PY_INDENT}if _held == {key}:",
            f"{_PY_INDENT * 2}{flag}.set(False)",
        ]
    return lines, tuple(flag for _, flag in held)


def retain(
    body: list[str],
    key: int,
    payload: str,
    state: str,
    insts: tuple[str, ...],
    message: str,
    consumed: str | None = None,
    arrival: str = _ARRIVAL,
) -> list[str]:
    """Retain a trigger while the source state's subtree is settling.

    A child whose busy port is present at this tag selected its
    transition in the same reaction cycle; ``_<state>_busy`` holds the
    children announced busy earlier that have not settled yet. The
    retained entry keeps the trigger key, captured payload and arrival
    tag. At most one occurrence is retained: a second arrival while one
    is held raises a runtime error rather than reorder or drop either.
    A retained signal marks its consumption flag as held (``None``):
    enclosing candidates still treat the occurrence as unconsumed, and
    a later region must not accept it meanwhile.

    Args:
        body: The immediate dispatch lines.
        key: The trigger's identity within its source state.
        payload: Python expression for the payload to retain.
        state: The resolved simple name of the source state.
        insts: The source state's child reactor instances.
        message: The diagnostic for overlapping retained occurrences.
        consumed: The signal's consumption-flag output, or None.
        arrival: Python expression for the occurrence's arrival tag.
    """
    busy = " or ".join(
        (
            f"self._{state}_busy",
            *(f"{inst}.{BUSY_PORT}.is_present" for inst in insts),
        )
    )
    pending = f"self._{state}_pending"
    return [
        f"_tag = {arrival}",
        f"if {busy}:",
        f"{_PY_INDENT}if {pending}:",
        f"{_PY_INDENT * 2}raise RuntimeError({json.dumps(message)})",
        f"{_PY_INDENT}{pending} = ({key}, {payload}, _tag)",
        *([f"{_PY_INDENT}{consumed}.set(None)"] if consumed else []),
        "else:",
        *(f"{_PY_INDENT}{line}" for line in body),
    ]


def overtaking(state: str, insts: tuple[str, ...], message: str) -> list[str]:
    """Reject a later occurrence accepted below a retaining state.

    The first line binds ``_newest``, the latest arrival tag the
    children report busy with at this tag; the rest raise a runtime
    error when it is later than the retained occurrence's arrival.

    Args:
        state: The resolved simple name of the source state.
        insts: The source state's child reactor instances.
        message: The diagnostic for a later accepted occurrence.
    """
    pending = f"self._{state}_pending"
    ports = "".join(f"{inst}.{BUSY_PORT}, " for inst in insts)
    return [
        f"_newest = max((p.value for p in ({ports}) "
        "if p.is_present), default=())",
        f"if {pending} and _newest > {pending}[2]:",
        f"{_PY_INDENT}raise RuntimeError({json.dumps(message)})",
    ]


def exit_handler(
    exits: list[tuple[str, list[str], tuple[str, ...]]],
    sent: list[str],
    ports: tuple[str, ...],
) -> Reaction:
    """Exit the active state of a scope on its parent's request.

    An active leaf runs its exit and acknowledges at once; an active
    composite/parallel state forwards the request to its first child
    and finishes in its drain chain. A scope that is already finalized
    (or not yet active) acknowledges without running an exit. The active
    slot holds a mode name while active, an empty string during selected
    cleanup, and None once exited. A request arriving during cleanup waits
    for its continuation to acknowledge rather than restarting its exits.

    Args:
        exits: Per mode, its forced-exit lines and their effects.
        sent: Signals sent in this scope.
        ports: Peer-accepted signals sent in this scope.
    """
    body = [f"self.{EXIT_PENDING_VAR} = True"]
    effects: dict[str, None] = {}
    for index, (mode, lines, mode_effects) in enumerate(exits):
        keyword = "if" if index == 0 else "elif"
        body.append(f'{keyword} self.{ACTIVE_VAR} == "{mode}":')
        body += [f"{_PY_INDENT}{line}" for line in lines]
        effects.update(dict.fromkeys(mode_effects))
    keyword = "elif" if exits else "if"
    body += [
        f"{keyword} self.{ACTIVE_VAR} is None:",
        f"{_PY_INDENT}{EXIT_ACK_PORT}.set(True)",
        f"{_PY_INDENT}self.{EXIT_PENDING_VAR} = False",
    ]
    effects[EXIT_ACK_PORT] = None
    return make_reaction(
        (EXIT_REQUEST_PORT,),
        tuple(effects),
        tuple(body),
        sent,
        ports,
    )


def lifecycle_reaction(
    state: str,
    insts: tuple[str, ...],
    retained: list[tuple[int, list[str], tuple[str, ...]]],
    consumed: list[tuple[int, tuple[str, ...]]],
    sent: list[str],
    held: list[tuple[int, str]],
    overtaking: list[str],
    *,
    report_upward: bool,
    ports: tuple[str, ...],
) -> Reaction:
    """Track a composite/parallel state's children until they settle.

    A child reports busy when it selects a transition and settled once
    its target entry, nested entries, eventless closure and natural
    region finalization are complete. Busy carries the arrival tag of
    the occurrence that selected the transition, or ``()`` for an
    eventless continuation. When no child is busy, the retained trigger
    dispatches with its captured payload and arrival tag; an all-false
    trigger is retired, not retried. A retained signal that a
    descendant consumed under the same arrival tag is dropped, because
    that descendant accepted the same occurrence. A descendant
    accepting a later occurrence while one is retained raises a runtime
    error rather than reorder the two. A reporting scope forwards busy,
    with the latest arrival tag, and settled upward.

    Args:
        state: The resolved simple name of the source state.
        insts: The source state's child reactor instances.
        retained: Per trigger, its key, payload-aware dispatch lines and
            their effects.
        consumed: Per signal trigger, its key and the descendant
            consumption ports it yields to.
        sent: Signals sent in this scope.
        held: Retained signal keys and their consumption flags; a
            retained signal whose guards are all false is declined.
        overtaking: Lines binding the latest arrival and rejecting overtaking.
        report_upward: Whether this scope reports busy and settled upward.
        ports: Peer-accepted signals sent in this scope.
    """
    busy = f"self._{state}_busy"
    pending = f"self._{state}_pending"
    body: list[str] = []
    for inst in insts:
        body += [
            f"if {inst}.{BUSY_PORT}.is_present:",
            f'{_PY_INDENT}{busy}.add("{inst}")',
            f"elif {inst}.{SETTLED_PORT}.is_present:",
            f'{_PY_INDENT}{busy}.discard("{inst}")',
        ]
    body += overtaking if retained else overtaking[:1]
    for key, sources in consumed:
        cond = " or ".join(
            f"({src}.is_present and {src}.value == {pending}[2])"
            for src in sources
        )
        body += [
            f"if {pending} and {pending}[0] == {key} and ({cond}):",
            f"{_PY_INDENT}{pending} = ()",
        ]
    effects: dict[str, None] = {}
    if retained:
        body += [
            f"if not {busy} and {pending}:",
            f"{_PY_INDENT}_key, _value, _tag = {pending}",
            f"{_PY_INDENT}{pending} = ()",
        ]
        flags = dict(held)
        for index, (key, lines, targets) in enumerate(retained):
            keyword = "if" if index == 0 else "elif"
            body.append(f"{_PY_INDENT}{keyword} _key == {key}:")
            body += [f"{_PY_INDENT * 2}{line}" for line in lines]
            effects.update(dict.fromkeys(targets))
            if key in flags:
                body += [
                    f"{_PY_INDENT * 2}if {pending} is not None:",
                    f"{_PY_INDENT * 3}{flags[key]}.set(False)",
                ]
    if report_upward:
        body += [
            f"if {busy}:",
            f"{_PY_INDENT}{report_busy('_newest')}",
            f"elif {pending} is not None:",
            f"{_PY_INDENT}{SETTLED_PORT}.set(True)",
        ]
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
    body: tuple[str, ...],
    sent: list[str],
    ports: tuple[str, ...] = (),
    *,
    sources: tuple[str, ...] = (),
) -> Reaction:
    """Build a reaction, declaring scheduled actions and set ports.

    lfc's Python target passes only the names listed in the effects
    clause into the reaction function, so every reactor-level logical
    action the body schedules and every output port the body sets must
    be declared as an effect.
    """
    # Substring scan over our own codegen output; a signal name that is
    # a suffix of another ("Tick"/"RetryTick") may add a spurious effect,
    # which LF treats as benign (the action is never triggered / the
    # port is never set).
    scheduled = (
        f"{sig}_act"
        for sig in sent
        if any(f"{sig}_act.schedule" in line for line in body)
    )
    set_ports = (
        sig for sig in ports if any(f"{sig}.set(" in line for line in body)
    )
    all_effects = tuple(dict.fromkeys((*effects, *scheduled, *set_ports)))
    return Reaction(triggers, all_effects, body, sources)
