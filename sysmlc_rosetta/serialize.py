from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sysmlc.backends.rosetta.program import LfProgram, Mode, Reaction

_INDENT = "  "
_UNITS: tuple[tuple[str, int], ...] = (
    ("sec", 10**9),
    ("msec", 10**6),
    ("usec", 10**3),
)


def render_duration(seconds: float) -> str:
    """Render SI seconds as the largest exact LF time value.

    LF time values are integers with a unit, so ``5.0`` -> ``"5 sec"`` and
    ``2.5`` -> ``"2500 msec"``; a duration that is not a whole number of
    microseconds falls back to nanoseconds.
    """
    nanoseconds = round(seconds * 1e9)
    for unit, factor in _UNITS:
        if nanoseconds % factor == 0:
            return f"{nanoseconds // factor} {unit}"
    return f"{nanoseconds} nsec"


def to_lf(program: LfProgram) -> str:
    """Serialize an ``LfProgram`` to Lingua Franca source text."""
    reactor = program.reactor
    lines: list[str] = ["target Python", ""]
    if program.preamble:
        lines.append("preamble {=")
        lines.extend(f"{_INDENT}{line}" for line in program.preamble)
        lines.extend(("=}", ""))
    # Parameter defaults and state initializers are wrapped in {= ... =}
    # unconditionally: lfc parses bare initializers as LF values and rejects
    # any non-literal Python (e.g. SimpleNamespace(...), LightColor.red).
    params = ", ".join(
        f"{p.name} = {{= {p.default} =}}" for p in reactor.parameters
    )
    header = (
        f"reactor {reactor.name}({params})"
        if params
        else f"reactor {reactor.name}"
    )
    lines.append(f"{header} {{")
    lines.extend(f"{_INDENT}input {name}" for name in reactor.inputs)
    lines.extend(f"{_INDENT}output {name}" for name in reactor.outputs)
    lines.extend(
        f"{_INDENT}state {var.name} = {{= {var.init} =}}"
        for var in reactor.state_vars
    )
    lines.extend(
        f"{_INDENT}logical action {action.name}" for action in reactor.actions
    )
    for reaction in reactor.reactions:
        lines.extend(_reaction_lines(reaction, depth=1))
    for mode in reactor.modes:
        lines.extend(_mode_lines(mode))
    lines.extend(("}", "", "main reactor {"))
    lines.append(f"{_INDENT}m = new {reactor.name}()")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _mode_lines(mode: Mode) -> list[str]:
    keyword = "initial mode" if mode.initial else "mode"
    lines = [f"{_INDENT}{keyword} {mode.name} {{"]
    lines.extend(
        f"{_INDENT * 2}timer {timer.name}({timer.offset})"
        for timer in mode.timers
    )
    lines.extend(
        f"{_INDENT * 2}logical action {action.name}" for action in mode.actions
    )
    for reaction in mode.reactions:
        lines.extend(_reaction_lines(reaction, depth=2))
    lines.append(f"{_INDENT}}}")
    return lines


def _reaction_lines(reaction: Reaction, depth: int) -> list[str]:
    pad = _INDENT * depth
    effects = f" -> {', '.join(reaction.effects)}" if reaction.effects else ""
    lines = [f"{pad}reaction({', '.join(reaction.triggers)}){effects} {{="]
    lines.extend(f"{pad}{_INDENT}{line}" for line in reaction.body)
    lines.append(f"{pad}=}}")
    return lines
