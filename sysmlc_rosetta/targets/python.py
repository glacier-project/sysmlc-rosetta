"""LF's Python target: rosetta's original spelling, byte for byte."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final, override

from sysmlc.codegen.structured import register_dataclass

from sysmlc_rosetta.lifecycle import ACTIVE_VAR, BUSY_PORT, OUTPUT_PORT
from sysmlc_rosetta.program import StateVar
from sysmlc_rosetta.statements import If, Pass, Stmt, nest
from sysmlc_rosetta.targets.base import LfTarget, StateKind, files_option

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import syside

    from sysmlc_rosetta.codegen import PreambleNeeds
    from sysmlc_rosetta.statements import Body

_INDENT: Final = "    "
_ARRIVAL: Final = "(lf.time.logical_elapsed(), lf.tag().microstep)"


class PythonTarget(LfTarget):
    """Spell reaction bodies for LF's Python target.

    Tags are ``(elapsed, microstep)`` tuples and ``()`` sorts before every
    tag; slots use ``None``/``()``/tuples; busy children are a set of
    instance names.
    """

    name = "python"
    lf_name = "Python"
    foreign_languages = ("python",)

    # -- program --

    @override
    def register_definition(
        self,
        definition: syside.Definition,
        needs: PreambleNeeds,
        render_default: Callable[[syside.Expression], str],
    ) -> None:
        register_dataclass(definition, needs.dataclasses, render_default)

    @override
    def register_signal(
        self, channel: str, definition: syside.Definition | None
    ) -> None:
        """Python ports are untyped: nothing to record."""

    @override
    def preamble_lines(self, needs: PreambleNeeds) -> list[str]:
        return needs.preamble_lines()

    @override
    def target_options(
        self, needs: PreambleNeeds
    ) -> tuple[tuple[str, str], ...]:
        companion = (
            f"{needs.types_module}.py"
            if needs.types_module and needs.companion_module_lines()
            else None
        )
        option = files_option(companion, needs.external)
        return () if option is None else (option,)

    @override
    def support_files(
        self, needs: PreambleNeeds
    ) -> tuple[tuple[str, str], ...]:
        """The types module travels as the program's ``types_module_*``."""
        return ()

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
        if kind == "busy":
            name = f"_{name}_busy"
        init = "False" if kind == "flag" else "None"
        return (StateVar(name, init, reset=reset),)

    # -- harness observation --

    @override
    def observe_entry(self, path: str) -> Stmt:
        return Stmt(
            'print("__SYSMLC_ENTRY__", '
            f"lf.time.logical_elapsed() // 1000000, {json.dumps(path)})"
        )

    @override
    def observe_value(self, attribute: str, ctype: str | None) -> Stmt:
        return Stmt(
            f'print("__SYSMLC_VALUE__", {json.dumps(attribute)}, '
            f"json.dumps(self.{attribute}))"
        )

    # -- rendering --

    @override
    def render(self, body: Body) -> tuple[str, ...]:
        return tuple(self._lines(body, ""))

    def _lines(self, body: Body, pad: str) -> list[str]:
        lines: list[str] = []
        for node in body:
            if isinstance(node, str):
                lines.append(f"{pad}{node}")
            elif isinstance(node, Stmt):
                lines.append(f"{pad}{node.text}")
            elif isinstance(node, Pass):
                lines.append(f"{pad}pass")
            else:
                lines += self._if_lines(node, pad)
        return lines

    def _if_lines(self, node: If, pad: str) -> list[str]:
        lines: list[str] = []
        for index, (condition, branch) in enumerate(node.branches):
            keyword = "if" if index == 0 else "elif"
            lines.append(f"{pad}{keyword} {condition}:")
            lines += self._lines(branch or (Pass(),), pad + _INDENT)
        if node.orelse is not None:
            lines.append(f"{pad}else:")
            lines += self._lines(node.orelse or (Pass(),), pad + _INDENT)
        return lines

    # -- values and conditions --

    @override
    def boolean(self, value: bool) -> str:
        return "True" if value else "False"

    @override
    def var(self, name: str) -> str:
        return f"self.{name}"

    @override
    def is_present(self, port: str) -> str:
        return f"{port}.is_present"

    @override
    def value(self, port: str) -> str:
        return f"{port}.value"

    @override
    def value_or(self, port: str, fallback: str) -> str:
        return f"({port}.value if {port}.is_present else {fallback})"

    @override
    def no_payload(self) -> str:
        return "None"

    @override
    def negate(self, condition: str) -> str:
        return f"not ({condition})"

    @override
    def not_var(self, name: str) -> str:
        return f"not self.{name}"

    @override
    def all_of(self, conditions: Sequence[str]) -> str:
        return " and ".join(conditions)

    @override
    def any_of(self, conditions: Sequence[str]) -> str:
        return " or ".join(conditions)

    # -- plain statements --

    @override
    def set_flag(self, name: str, value: bool) -> Stmt:
        return Stmt(f"self.{name} = {self.boolean(value)}")

    @override
    def set_port(self, port: str, value: str) -> Stmt:
        return Stmt(f"{port}.set({value})", writes=frozenset((port,)))

    @override
    def set_mode(self, mode: str) -> Stmt:
        return Stmt(f"{mode}.set()")

    @override
    def schedule(
        self, action: str, delay: str = "0", value: str | None = None
    ) -> Stmt:
        arguments = delay if value is None else f"{delay}, {value}"
        return Stmt(
            f"{action}.schedule({arguments})", writes=frozenset((action,))
        )

    @override
    def fail(self, message: str) -> Stmt:
        return Stmt(f"raise RuntimeError({json.dumps(message)})")

    @override
    def request_stop(self) -> Stmt:
        return Stmt("request_stop()")

    @override
    def log(self, message: str) -> Stmt:
        return Stmt(f'logging.debug("{message}")')

    @override
    def check(self, condition: str, message: str) -> Stmt:
        return Stmt(f"assert {condition}, {json.dumps(message)}")

    # -- current state announcements --

    @override
    def announce(self, path: str, scope_path: str) -> Stmt:
        return self.set_port(OUTPUT_PORT, json.dumps(path))

    @override
    def reemit(self, inst: str, prefix: str) -> Stmt:
        return self.set_port(
            OUTPUT_PORT, f'"{prefix}" + {inst}.{OUTPUT_PORT}.value'
        )

    # -- time --

    @override
    def arrival(self) -> str:
        return _ARRIVAL

    @override
    def continuation(self) -> str:
        return "()"

    @override
    def seconds_to_ns(self, seconds: str) -> str:
        return f"int(({seconds}) * 1e9)"

    @override
    def bind_after(self, delay_ns: str) -> Stmt:
        return Stmt(f"_after_ns = {delay_ns}")

    @override
    def bind_at_delta(self, instant_ns: str) -> Stmt:
        return Stmt(f"_at_delta = {instant_ns} - lf.time.logical_elapsed()")

    @override
    def arm_due(self, due: str, delay_ns: str) -> Stmt:
        return Stmt(f"self.{due} = lf.time.logical_elapsed() + {delay_ns}")

    @override
    def arm_at_due(self, due: str) -> Stmt:
        return Stmt(
            f"self.{due} = lf.time.logical_elapsed() + _at_delta "
            "if _at_delta >= 0 else None"
        )

    @override
    def due_reached(self, due: str) -> str:
        return f"lf.time.logical_elapsed() == self.{due}"

    @override
    def clear_due(self, due: str) -> Stmt:
        return Stmt(f"self.{due} = None")

    @override
    def mark_since(self, since: str) -> Stmt:
        return Stmt(f"self.{since} = {_ARRIVAL}")

    @override
    def arrived_since(self, occurrence: str, since: str) -> str:
        return f"{occurrence} >= self.{since}"

    # -- children busy tracking --

    @override
    def busy_init(self, state: str, insts: Sequence[str]) -> Body:
        entering = ", ".join(json.dumps(inst) for inst in insts)
        return (Stmt(f"self._{state}_busy = {{{entering}}}"),)

    @override
    def busy_add(self, state: str, inst: str) -> Stmt:
        return Stmt(f'self._{state}_busy.add("{inst}")')

    @override
    def busy_discard(self, state: str, inst: str) -> Stmt:
        return Stmt(f'self._{state}_busy.discard("{inst}")')

    @override
    def busy_any(self, state: str, insts: Sequence[str]) -> str:
        return f"self._{state}_busy"

    @override
    def busy_none(self, state: str, insts: Sequence[str]) -> str:
        return f"not self._{state}_busy"

    @override
    def report_busy(self, occurrence: str) -> Stmt:
        return self.set_port(
            BUSY_PORT,
            f"max({BUSY_PORT}.value, {occurrence}) "
            f"if {BUSY_PORT}.is_present else {occurrence}",
        )

    @override
    def bind_value(self, value: str, signal: str) -> Body:
        return (Stmt(f"_value = {value}"),)

    @override
    def bind_tag(self, occurrence: str) -> Stmt:
        return Stmt(f"_tag = {occurrence}")

    @override
    def bind_newest(self, insts: Sequence[str]) -> Body:
        ports = "".join(f"{inst}.{BUSY_PORT}, " for inst in insts)
        return (
            Stmt(
                f"_newest = max((p.value for p in ({ports}) "
                "if p.is_present), default=())"
            ),
        )

    # -- pending slot --

    @override
    def pending_holding(self, pending: str) -> str:
        return f"self.{pending}"

    @override
    def pending_closed(self, pending: str) -> str:
        return f"self.{pending} is None"

    @override
    def pending_not_closed(self, pending: str) -> str:
        return f"self.{pending} is not None"

    @override
    def pending_open(self, pending: str) -> Stmt:
        return Stmt(f"self.{pending} = ()")

    @override
    def pending_close(self, pending: str) -> Stmt:
        return Stmt(f"self.{pending} = None")

    @override
    def pending_hold(
        self, pending: str, key: int, payload: str, signal: str | None
    ) -> Stmt:
        return Stmt(f"self.{pending} = ({key}, {payload}, _tag)")

    @override
    def pending_tag(self, pending: str) -> str:
        return f"self.{pending}[2]"

    @override
    def pending_later_than(self, pending: str, occurrence: str) -> str:
        return f"self.{pending} and {occurrence} > self.{pending}[2]"

    @override
    def pending_key_is(self, pending: str, key: int) -> str:
        return f"self.{pending} and self.{pending}[0] == {key}"

    @override
    def pending_take(self, pending: str) -> Body:
        return (Stmt(f"_key, _value, _tag = self.{pending}"),)

    @override
    def bind_held(self, pending: str) -> Stmt:
        return Stmt(f"_held = self.{pending}[0] if self.{pending} else None")

    # -- selection slot --

    @override
    def sel_idle(self, sel: str) -> Stmt:
        return Stmt(f"self.{sel} = None")

    @override
    def sel_ancestor(self, sel: str) -> Stmt:
        return Stmt(f"self.{sel} = ()")

    @override
    def sel_select(
        self, sel: str, key: int, payload: str, signal: str | None
    ) -> Stmt:
        return Stmt(f"self.{sel} = ({key}, {payload})")

    @override
    def unpack_value(self, channels: Sequence[str]) -> Body:
        """Taking the slot already bound ``_value``."""
        return ()

    @override
    def sel_busy(self, sel: str) -> str:
        return f"self.{sel} is not None"

    @override
    def sel_selected(self, sel: str) -> str:
        return f"self.{sel}"

    @override
    def sel_take(self, sel: str) -> Body:
        return (Stmt(f"_key, _value = self.{sel}"),)

    # -- active mode of a reporting scope --

    @override
    def active_is(self, mode: str) -> str:
        return f'self.{ACTIVE_VAR} == "{mode}"'

    @override
    def active_is_none(self) -> str:
        return f"self.{ACTIVE_VAR} is None"

    @override
    def set_active(self, mode: str | None) -> Stmt:
        value = "None" if mode is None else f'"{mode}"'
        return Stmt(f"self.{ACTIVE_VAR} = {value}")

    # -- consumption flags --

    @override
    def consume(self, flag: str, outcome: str) -> Stmt:
        return self.set_port(flag, outcome)

    @override
    def accepted(self) -> str:
        return "True"

    @override
    def held(self) -> str:
        return "None"

    @override
    def declined(self) -> str:
        return "False"

    @override
    def consumed_at(self, flag: str, occurrence: str) -> str:
        return f"({flag}.is_present and {flag}.value == {occurrence})"

    @override
    def consumption_decided(self, flag: str) -> str:
        return f"({flag}.is_present and {flag}.value not in (None, False))"

    @override
    def forward_consumption(
        self, flag: str, source: str, pending: str | None, keys: Sequence[int]
    ) -> Body:
        if pending is None or not keys:
            return (self.set_port(flag, f"{source}.value"),)
        listed = "".join(f"{key}, " for key in keys)
        return (
            Stmt(f"_value = {source}.value"),
            nest(
                f"_value is False and self.{pending} and "
                f"self.{pending}[0] in ({listed})",
                (Stmt("_value = None"),),
            ),
            self.set_port(flag, "_value"),
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
        ports = ", ".join(
            f'("{child}", {child}.{sig}_consumed)' for child in earlier
        )
        held = f"self.{wait}"
        forward = nest(
            f"{held} is not None and not _holders",
            (
                Stmt(f"{held} = None"),
                *self.write_event(f"{inst}.{sig}", "_value"),
                self.set_port(f"{inst}.{sig}_origin", "_tag"),
            ),
        )
        return (
            nest(
                f"{sig}.is_present",
                (
                    nest(f"{held} is not None", (self.fail(message),)),
                    Stmt(f"{held} = [{sig}.value, {arrival}, set()]"),
                ),
            ),
            nest(
                f"{held} is not None",
                (
                    Stmt(f"_value, _tag, _holders = {held}"),
                    # A loop has no tree node: verbatim lines, with their
                    # indentation relative to this block.
                    f"for _name, _port in ({ports},):",
                    f"{_INDENT}if not _port.is_present:",
                    f"{_INDENT * 2}continue",
                    f"{_INDENT}if _port.value is None:",
                    f"{_INDENT * 2}_holders.add(_name)",
                    f"{_INDENT}elif _port.value is False:",
                    f"{_INDENT * 2}_holders.discard(_name)",
                    f"{_INDENT}elif _port.value is True or _port.value == _tag:",
                    f"{_INDENT * 2}{held} = None",
                    forward,
                ),
            ),
        )

    @override
    def clear_wait(self, wait: str) -> Stmt:
        return Stmt(f"self.{wait} = None")
