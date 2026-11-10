"""LF's C target (reactor-c), with no dynamic allocation.

Python idioms of the run-to-completion protocol map to fixed-size C:

- a tag is reactor-c's ``tag_t``; "no tag" is ``NEVER_TAG``;
- a pending or selection slot is a struct with an explicit state, its key,
  its tag and a payload union (``sysmlc_payload_t``, in the types header);
- the busy children of a state are one ``bool`` per child;
- a consumption flag is a ``sysmlc_consumption_t`` (outcome and tag);
- the active mode is an ``int``: 0 none, 1 cleanup, 2 and up a mode;
- ``current_state`` is a ``string`` port set only from literals: each
  reactor announces its full path within the machine.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Final, override

from sysmlc.codegen.c import c_string
from sysmlc.errors import UnsupportedConstructError

from sysmlc_rosetta.c_types import CTypes, is_reserved
from sysmlc_rosetta.lifecycle import (
    ACTIVE_VAR,
    BUSY_PORT,
    COMPLETED_PORT,
    ENTRY_ORDER_PORT,
    EXIT_ACK_PORT,
    EXIT_REQUEST_PORT,
    OUTPUT_PORT,
    SETTLED_PORT,
)
from sysmlc_rosetta.program import LogicalAction, StateVar
from sysmlc_rosetta.statements import If, Pass, Stmt, nest
from sysmlc_rosetta.targets.base import (
    CLEANUP,
    LfTarget,
    StateKind,
    files_option,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import syside
    from sysmlc.semantics.statemachine.facts import AttributeBinding

    from sysmlc_rosetta.codegen import PreambleNeeds
    from sysmlc_rosetta.program import Reactor
    from sysmlc_rosetta.statements import Body, Node

_INDENT: Final = "  "
_BOOL_PORTS: Final = frozenset(
    {
        COMPLETED_PORT,
        ENTRY_ORDER_PORT,
        SETTLED_PORT,
        EXIT_REQUEST_PORT,
        EXIT_ACK_PORT,
    }
)
_EXIT_PORT: Final = re.compile(r"exit_\d+(_done)?")
_ACCEPTED: Final = "SYSMLC_ACCEPTED"
_HELD: Final = "SYSMLC_HELD"
_DECLINED: Final = "SYSMLC_DECLINED"
_NONE_ACTIVE: Final = 0
_CLEANUP_ACTIVE: Final = 1

#: Protocol types every C program declares after its types header.
PROTOCOL_TYPES: Final = (
    "typedef enum { SYSMLC_CLOSED, SYSMLC_OPEN, SYSMLC_HOLDING } "
    "sysmlc_slot_t;",
    "typedef struct { sysmlc_slot_t slot; int key; tag_t tag; "
    "sysmlc_payload_t value; } sysmlc_pending_t;",
    "typedef enum { SYSMLC_IDLE, SYSMLC_ANCESTOR, SYSMLC_SELECTED } "
    "sysmlc_selection_state_t;",
    "typedef struct { sysmlc_selection_state_t state; int key; "
    "sysmlc_payload_t value; } sysmlc_selection_t;",
    "typedef enum { SYSMLC_DECLINED, SYSMLC_HELD, SYSMLC_ACCEPTED } "
    "sysmlc_outcome_t;",
    "typedef struct { sysmlc_outcome_t outcome; tag_t tag; } "
    "sysmlc_consumption_t;",
    "typedef struct { bool active; tag_t tag; sysmlc_payload_t value; } "
    "sysmlc_wait_t;",
)


class CTarget(LfTarget):
    """Spell reaction bodies for LF's C target.

    One instance serves one program: it collects the C types the program
    uses (:attr:`types`) and numbers its modes.
    """

    name = "c"
    lf_name = "C"
    foreign_languages = ("c", "c_h")

    def __init__(self) -> None:
        self.types = CTypes()
        self._mode_ids: dict[str, int] = {}

    # -- program --

    @override
    def register_definition(
        self,
        definition: syside.Definition,
        needs: PreambleNeeds,
        render_default: Callable[[syside.Expression], str],
    ) -> None:
        self.types.register(definition)

    @override
    def register_signal(
        self, channel: str, definition: syside.Definition | None
    ) -> None:
        self.types.set_signal(channel, definition)

    @override
    def attribute_type(self, binding: AttributeBinding) -> tuple[str, str]:
        ctype = self.types.attribute_type(binding)
        return ctype, self.types.zero(ctype)

    @override
    def declare(self, reactor: Reactor) -> Reactor:
        """Give every port and logical action of ``reactor`` its C type.

        Raises:
            UnsupportedConstructError: If a port, mode or state variable is
                named like a C keyword or a name the generated C uses.
        """
        ports = (*reactor.inputs, *reactor.outputs)
        names = (
            *ports,
            *(mode.name for mode in reactor.modes),
            *(var.name for var in reactor.state_vars),
        )
        reserved = sorted(name for name in set(names) if is_reserved(name))
        if reserved:
            raise UnsupportedConstructError(
                f"names {reserved!r} in {reactor.name!r} are C keywords or "
                "names the generated C uses; rename them for the C target"
            )
        return replace(
            reactor,
            port_types=tuple((port, self.port_type(port)) for port in ports),
            actions=self._typed(reactor.actions),
            modes=tuple(
                replace(mode, actions=self._typed(mode.actions))
                for mode in reactor.modes
            ),
        )

    def port_type(self, port: str) -> str:
        """The C type of one of rosetta's generated ports, by its name."""
        if port.endswith(OUTPUT_PORT):
            return "string"
        if port in _BOOL_PORTS or _EXIT_PORT.fullmatch(port):
            return "bool"
        if port == BUSY_PORT or port.endswith("_origin"):
            return "tag_t"
        if port.endswith("_consumed"):
            return "sysmlc_consumption_t"
        return self.types.signal_type(port) or "bool"

    def _typed(
        self, actions: tuple[LogicalAction, ...]
    ) -> tuple[LogicalAction, ...]:
        """Give each ``<signal>_act`` action its payload type, if any."""
        return tuple(
            replace(
                action,
                type=self.types.signal_type(action.name.removesuffix("_act"))
                if action.name.endswith("_act")
                else None,
            )
            for action in actions
        )

    @override
    def preamble_lines(self, needs: PreambleNeeds) -> list[str]:
        lines = ["#include <stdio.h>", "#include <stdlib.h>"]
        if needs.uses_math:
            lines.append("#include <math.h>")
        lines.append(f'#include "{needs.types_module}.h"')
        lines += [
            f'#include "{artifact.path.name}"'
            for artifact in needs.external
            if artifact.lang == "c_h"
        ]
        return [*lines, *PROTOCOL_TYPES]

    @override
    def target_options(
        self, needs: PreambleNeeds
    ) -> tuple[tuple[str, str], ...]:
        files = files_option(f"{needs.types_module}.h", needs.external)
        assert files is not None  # the types header always ships
        options = [files]
        if any(artifact.lang == "c" for artifact in needs.external):
            options.append(("cmake-include", f'"{needs.types_module}.cmake"'))
        return tuple(options)

    @override
    def support_files(
        self, needs: PreambleNeeds
    ) -> tuple[tuple[str, str], ...]:
        header = self.types.header_lines(f"{needs.types_module}_H".upper())
        files = [(f"{needs.types_module}.h", "\n".join(header) + "\n")]
        sources = [a.path.name for a in needs.external if a.lang == "c"]
        if sources:
            files.append(
                (
                    f"{needs.types_module}.cmake",
                    "target_sources(${LF_MAIN_TARGET} PRIVATE "
                    + " ".join(sources)
                    + ")\n",
                )
            )
        files += [(a.path.name, a.path.read_text()) for a in needs.external]
        return tuple(files)

    # -- declarations --

    @override
    def state_vars(
        self,
        name: str,
        kind: StateKind,
        *,
        reset: bool,
        insts: Sequence[str] = (),
    ) -> tuple[StateVar, ...]:
        if kind == "flag":
            return (StateVar(name, "false", reset=reset, type="bool"),)
        if kind == "busy":
            return tuple(
                StateVar(
                    self._busy(name, inst), "false", reset=reset, type="bool"
                )
                for inst in insts
            )
        if kind == "wait":
            return (
                StateVar(
                    name,
                    "(sysmlc_wait_t){ 0 }",
                    reset=reset,
                    type="sysmlc_wait_t",
                ),
                *(
                    StateVar(
                        f"{name}_{inst}", "false", reset=reset, type="bool"
                    )
                    for inst in insts
                ),
            )
        init, ctype = {
            "due": ("NEVER", "instant_t"),
            "since": ("NEVER_TAG", "tag_t"),
            "pending": ("(sysmlc_pending_t){ 0 }", "sysmlc_pending_t"),
            "selection": ("(sysmlc_selection_t){ 0 }", "sysmlc_selection_t"),
            "active": (str(_NONE_ACTIVE), "int"),
        }[kind]
        return (StateVar(name, init, reset=reset, type=ctype),)

    # -- harness observation --

    @override
    def observe_entry(self, path: str) -> Stmt:
        return Stmt(
            'printf("__SYSMLC_ENTRY__ %lld %s\\n", '
            "(long long)(lf_time_logical_elapsed() / MSEC(1)), "
            f"{c_string(path)});"
        )

    @override
    def observe_value(self, attribute: str, ctype: str | None) -> Stmt:
        conversion, argument = self.types.print_format(ctype or "bool")
        return Stmt(
            f'printf("__SYSMLC_VALUE__ {attribute} {conversion}\\n", '
            f"{argument(self.var(attribute))});"
        )

    # -- rendering --

    @override
    def render(self, body: Body) -> tuple[str, ...]:
        return tuple(self._lines(body, "", set()))

    def _lines(self, body: Body, pad: str, declared: set[str]) -> list[str]:
        """Render a block; a local's first assignment declares it."""
        declared = set(declared)
        lines: list[str] = []
        for node in body:
            if isinstance(node, Pass):
                continue
            if isinstance(node, If):
                lines += self._if_lines(node, pad, declared)
                continue
            if isinstance(node, str):
                lines.append(f"{pad}{node}")
                continue
            text = node.text
            if node.local is not None:
                ctype, name = node.local
                if name not in declared:
                    text = f"{ctype} {text}"
                    declared.add(name)
            lines.append(f"{pad}{text}")
        return lines

    def _if_lines(self, node: If, pad: str, declared: set[str]) -> list[str]:
        inner = pad + _INDENT
        lines: list[str] = []
        for index, (condition, branch) in enumerate(node.branches):
            opening = "if" if index == 0 else "} else if"
            lines.append(f"{pad}{opening} ({condition}) {{")
            lines += self._lines(branch, inner, declared)
        if node.orelse is not None:
            lines.append(f"{pad}}} else {{")
            lines += self._lines(node.orelse, inner, declared)
        lines.append(f"{pad}}}")
        return lines

    # -- values and conditions --

    @override
    def boolean(self, value: bool) -> str:
        return "true" if value else "false"

    @override
    def var(self, name: str) -> str:
        return f"self->{name}"

    @override
    def is_present(self, port: str) -> str:
        return f"{port}->is_present"

    @override
    def value(self, port: str) -> str:
        return f"{port}->value"

    @override
    def value_or(self, port: str, fallback: str) -> str:
        return f"({port}->is_present ? {port}->value : {fallback})"

    @override
    def no_payload(self) -> str:
        return "0"

    @override
    def negate(self, condition: str) -> str:
        return f"!({condition})"

    @override
    def not_var(self, name: str) -> str:
        return f"!self->{name}"

    @override
    def all_of(self, conditions: Sequence[str]) -> str:
        return " && ".join(conditions)

    @override
    def any_of(self, conditions: Sequence[str]) -> str:
        return " || ".join(conditions)

    # -- plain statements --

    @override
    def set_flag(self, name: str, value: bool) -> Stmt:
        return Stmt(f"self->{name} = {self.boolean(value)};")

    @override
    def set_port(self, port: str, value: str) -> Stmt:
        # The value may be a compound literal: parentheses keep its commas
        # out of the lf_set macro's argument list.
        return Stmt(f"lf_set({port}, ({value}));", writes=frozenset((port,)))

    @override
    def set_mode(self, mode: str) -> Stmt:
        return Stmt(f"lf_set_mode({mode});")

    @override
    def schedule(
        self, action: str, delay: str = "0", value: str | None = None
    ) -> Stmt:
        text = (
            f"lf_schedule({action}, {delay});"
            if value is None
            else f"lf_schedule_copy({action}, {delay}, &({value}), 1);"
        )
        return Stmt(text, writes=frozenset((action,)))

    @override
    def fail(self, message: str) -> Stmt:
        return Stmt(f'lf_print_error_and_exit("%s", {c_string(message)});')

    @override
    def request_stop(self) -> Stmt:
        return Stmt("lf_request_stop();")

    @override
    def log(self, message: str) -> Stmt:
        return Stmt(f'LF_PRINT_DEBUG("%s", {c_string(message)});')

    @override
    def check(self, condition: str, message: str) -> Node:
        return nest(
            self.negate(condition),
            (
                Stmt(
                    'fprintf(stderr, "AssertionError: %s\\n", '
                    f"{c_string(message)});"
                ),
                Stmt("exit(1);"),
            ),
        )

    # -- current state announcements --

    @override
    def announce(self, path: str, scope_path: str) -> Stmt:
        full = f"{scope_path}.{path}" if scope_path else path
        return self.set_port(OUTPUT_PORT, c_string(full))

    @override
    def reemit(self, inst: str, prefix: str) -> Stmt:
        """Children announce full paths, so the parent forwards them as-is."""
        return self.set_port(OUTPUT_PORT, f"{inst}.{OUTPUT_PORT}->value")

    # -- time --

    @override
    def arrival(self) -> str:
        return "lf_tag()"

    @override
    def continuation(self) -> str:
        return "NEVER_TAG"

    @override
    def seconds_to_ns(self, seconds: str) -> str:
        return f"(interval_t)(({seconds}) * 1e9)"

    @override
    def bind_after(self, delay_ns: str) -> Stmt:
        return _local("interval_t", "_after_ns", delay_ns)

    @override
    def bind_at_delta(self, instant_ns: str) -> Stmt:
        return _local(
            "interval_t",
            "_at_delta",
            f"{instant_ns} - lf_time_logical_elapsed()",
        )

    @override
    def arm_due(self, due: str, delay_ns: str) -> Stmt:
        return Stmt(f"self->{due} = lf_time_logical_elapsed() + {delay_ns};")

    @override
    def arm_at_due(self, due: str) -> Stmt:
        return Stmt(
            f"self->{due} = _at_delta >= 0 ? "
            "lf_time_logical_elapsed() + _at_delta : NEVER;"
        )

    @override
    def due_reached(self, due: str) -> str:
        return f"lf_time_logical_elapsed() == self->{due}"

    @override
    def clear_due(self, due: str) -> Stmt:
        return Stmt(f"self->{due} = NEVER;")

    @override
    def mark_since(self, since: str) -> Stmt:
        return Stmt(f"self->{since} = lf_tag();")

    @override
    def arrived_since(self, occurrence: str, since: str) -> str:
        return f"lf_tag_compare({occurrence}, self->{since}) >= 0"

    # -- children busy tracking --

    def _busy(self, state: str, inst: str) -> str:
        return f"_{state}_{inst}_busy"

    @override
    def busy_init(self, state: str, insts: Sequence[str]) -> Body:
        return tuple(
            self.set_flag(self._busy(state, inst), True) for inst in insts
        )

    @override
    def busy_add(self, state: str, inst: str) -> Stmt:
        return self.set_flag(self._busy(state, inst), True)

    @override
    def busy_discard(self, state: str, inst: str) -> Stmt:
        return self.set_flag(self._busy(state, inst), False)

    @override
    def busy_any(self, state: str, insts: Sequence[str]) -> str:
        flags = " || ".join(self.var(self._busy(state, inst)) for inst in insts)
        return f"({flags})"

    @override
    def busy_none(self, state: str, insts: Sequence[str]) -> str:
        return f"!{self.busy_any(state, insts)}"

    @override
    def report_busy(self, occurrence: str) -> Stmt:
        return self.set_port(
            BUSY_PORT,
            f"{BUSY_PORT}->is_present && "
            f"lf_tag_compare({BUSY_PORT}->value, {occurrence}) > 0 "
            f"? {BUSY_PORT}->value : {occurrence}",
        )

    @override
    def bind_value(self, value: str, signal: str) -> Body:
        """Bind a payload; a field-less signal carries none to bind."""
        ctype = self.types.signal_type(signal)
        return () if ctype is None else (_local(ctype, "_value", value),)

    @override
    def bind_tag(self, occurrence: str) -> Stmt:
        return _local("tag_t", "_tag", occurrence)

    @override
    def bind_newest(self, insts: Sequence[str]) -> Body:
        newest = self.newest()
        keep = tuple(
            nest(
                f"{inst}.{BUSY_PORT}->is_present && "
                f"lf_tag_compare({inst}.{BUSY_PORT}->value, {newest}) > 0",
                (Stmt(f"{newest} = {inst}.{BUSY_PORT}->value;"),),
            )
            for inst in insts
        )
        return (_local("tag_t", newest, "NEVER_TAG"), *keep)

    # -- pending slot --

    @override
    def pending_holding(self, pending: str) -> str:
        return f"self->{pending}.slot == SYSMLC_HOLDING"

    @override
    def pending_closed(self, pending: str) -> str:
        return f"self->{pending}.slot == SYSMLC_CLOSED"

    @override
    def pending_not_closed(self, pending: str) -> str:
        return f"self->{pending}.slot != SYSMLC_CLOSED"

    @override
    def pending_open(self, pending: str) -> Stmt:
        return Stmt(f"self->{pending}.slot = SYSMLC_OPEN;")

    @override
    def pending_close(self, pending: str) -> Stmt:
        return Stmt(f"self->{pending}.slot = SYSMLC_CLOSED;")

    @override
    def pending_hold(
        self, pending: str, key: int, payload: str, signal: str | None
    ) -> Stmt:
        return Stmt(
            f"self->{pending} = (sysmlc_pending_t){{ .slot = SYSMLC_HOLDING, "
            f".key = {key}, .tag = _tag{self._stored(signal, payload)} }};"
        )

    @override
    def pending_tag(self, pending: str) -> str:
        return f"self->{pending}.tag"

    @override
    def pending_later_than(self, pending: str, occurrence: str) -> str:
        return (
            f"self->{pending}.slot == SYSMLC_HOLDING && "
            f"lf_tag_compare({occurrence}, self->{pending}.tag) > 0"
        )

    @override
    def pending_key_is(self, pending: str, key: int) -> str:
        return (
            f"self->{pending}.slot == SYSMLC_HOLDING && "
            f"self->{pending}.key == {key}"
        )

    @override
    def pending_take(self, pending: str) -> Body:
        return (
            _local("int", "_key", f"self->{pending}.key"),
            _local("tag_t", "_tag", f"self->{pending}.tag"),
            _local("sysmlc_payload_t", "_payload", f"self->{pending}.value"),
        )

    @override
    def bind_held(self, pending: str) -> Stmt:
        return _local(
            "int",
            "_held",
            f"self->{pending}.slot == SYSMLC_HOLDING ? self->{pending}.key : -1",
        )

    # -- selection slot --

    @override
    def sel_idle(self, sel: str) -> Stmt:
        return Stmt(f"self->{sel}.state = SYSMLC_IDLE;")

    @override
    def sel_ancestor(self, sel: str) -> Stmt:
        return Stmt(
            f"self->{sel} = (sysmlc_selection_t){{ .state = SYSMLC_ANCESTOR }};"
        )

    @override
    def sel_select(
        self, sel: str, key: int, payload: str, signal: str | None
    ) -> Stmt:
        return Stmt(
            f"self->{sel} = (sysmlc_selection_t){{ .state = SYSMLC_SELECTED, "
            f".key = {key}{self._stored(signal, payload)} }};"
        )

    @override
    def unpack_value(self, channels: Sequence[str]) -> Body:
        """Bind ``_value`` from the payload union.

        Raises:
            UnsupportedConstructError: If the payload may have arrived on
                channels of different C types: the union member to read is
                then known only at run time.
        """
        ctypes = {self.types.signal_type(channel) for channel in channels}
        if len(ctypes) > 1:
            raise UnsupportedConstructError(
                f"a payload retained or drained from channels {list(channels)!r}"
                " of different C types is not supported by the C target"
            )
        (ctype,) = ctypes
        if ctype is None:
            return ()
        return (_local(ctype, "_value", f"_payload.{channels[0]}"),)

    def _stored(self, signal: str | None, payload: str) -> str:
        """The payload union initializer of a slot, if a payload is kept."""
        if signal is None or self.types.signal_type(signal) is None:
            return ""
        return f", .value.{signal} = {payload}"

    @override
    def sel_busy(self, sel: str) -> str:
        return f"self->{sel}.state != SYSMLC_IDLE"

    @override
    def sel_selected(self, sel: str) -> str:
        return f"self->{sel}.state == SYSMLC_SELECTED"

    @override
    def sel_take(self, sel: str) -> Body:
        return (
            _local("int", "_key", f"self->{sel}.key"),
            _local("sysmlc_payload_t", "_payload", f"self->{sel}.value"),
        )

    # -- active mode of a reporting scope --

    def _mode_id(self, mode: str) -> int:
        return self._mode_ids.setdefault(mode, len(self._mode_ids) + 2)

    @override
    def active_is(self, mode: str) -> str:
        return f"self->{ACTIVE_VAR} == {self._mode_id(mode)}"

    @override
    def active_is_none(self) -> str:
        return f"self->{ACTIVE_VAR} == {_NONE_ACTIVE}"

    @override
    def set_active(self, mode: str | None) -> Stmt:
        if mode is None:
            value = _NONE_ACTIVE
        elif mode == CLEANUP:
            value = _CLEANUP_ACTIVE
        else:
            value = self._mode_id(mode)
        return Stmt(f"self->{ACTIVE_VAR} = {value};")

    # -- consumption flags --

    @override
    def consume(self, flag: str, outcome: str) -> Stmt:
        if outcome in {_ACCEPTED, _HELD, _DECLINED}:
            value = f"(sysmlc_consumption_t){{ {outcome}, NEVER_TAG }}"
        else:
            value = f"(sysmlc_consumption_t){{ {_ACCEPTED}, {outcome} }}"
        return self.set_port(flag, value)

    @override
    def accepted(self) -> str:
        return _ACCEPTED

    @override
    def held(self) -> str:
        return _HELD

    @override
    def declined(self) -> str:
        return _DECLINED

    @override
    def consumed_at(self, flag: str, occurrence: str) -> str:
        return (
            f"({flag}->is_present && {flag}->value.outcome == {_ACCEPTED} && "
            f"lf_tag_compare({flag}->value.tag, {occurrence}) == 0)"
        )

    @override
    def consumption_decided(self, flag: str) -> str:
        return f"({flag}->is_present && {flag}->value.outcome == {_ACCEPTED})"

    @override
    def forward_consumption(
        self, flag: str, source: str, pending: str | None, keys: Sequence[int]
    ) -> Body:
        if pending is None or not keys:
            return (self.set_port(flag, f"{source}->value"),)
        listed = " || ".join(f"self->{pending}.key == {key}" for key in keys)
        return (
            _local("sysmlc_consumption_t", "_consumption", f"{source}->value"),
            nest(
                f"_consumption.outcome == {_DECLINED} && "
                f"self->{pending}.slot == SYSMLC_HOLDING && ({listed})",
                (Stmt(f"_consumption.outcome = {_HELD};"),),
            ),
            self.set_port(flag, "_consumption"),
        )

    # -- parallel regions sharing a signal --

    @override
    def region_gate(
        self,
        *,
        sig: str,
        wait: str,
        inst: str,
        earlier: Sequence[str],
        arrival: str,
        message: str,
    ) -> Body:
        payload = self.types.signal_type(sig)
        value = "" if payload is None else f", .value.{sig} = {sig}->value"
        held = f"self->{wait}"
        holders = [f"self->{wait}_{child}" for child in earlier]
        start: list[Node] = [
            nest(f"{held}.active", (self.fail(message),)),
            Stmt(
                f"{held} = (sysmlc_wait_t){{ .active = true, "
                f".tag = {arrival}{value} }};"
            ),
            *(Stmt(f"{holder} = false;") for holder in holders),
        ]
        passes: list[Node] = []
        for child, holder in zip(earlier, holders, strict=True):
            port = f"{child}.{sig}_consumed"
            outcome = f"{port}->value.outcome"
            accepted = (
                f"{outcome} == {_ACCEPTED} && ({port}->value.tag.time == NEVER "
                f"|| lf_tag_compare({port}->value.tag, {held}.tag) == 0)"
            )
            branches = If(
                (
                    (f"{outcome} == {_HELD}", (Stmt(f"{holder} = true;"),)),
                    (
                        f"{outcome} == {_DECLINED}",
                        (Stmt(f"{holder} = false;"),),
                    ),
                    (accepted, (Stmt(f"{held}.active = false;"),)),
                )
            )
            passes.append(nest(self.is_present(port), (branches,)))
        unheld = " && ".join((f"{held}.active", *(f"!{h}" for h in holders)))
        forwarded = None if payload is None else f"{held}.value.{sig}"
        passes.append(
            nest(
                unheld,
                (
                    Stmt(f"{held}.active = false;"),
                    *self.write_event(f"{inst}.{sig}", forwarded),
                    self.set_port(f"{inst}.{sig}_origin", f"{held}.tag"),
                ),
            )
        )
        return (
            nest(self.is_present(sig), tuple(start)),
            nest(f"{held}.active", tuple(passes)),
        )

    @override
    def clear_wait(self, wait: str) -> Stmt:
        return Stmt(f"self->{wait}.active = false;")


def _local(ctype: str, name: str, value: str) -> Stmt:
    """Assign a local, declared ``ctype`` where it is first assigned."""
    return Stmt(f"{name} = {value};", local=(ctype, name))
