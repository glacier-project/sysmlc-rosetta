from __future__ import annotations

from typing import TYPE_CHECKING

from sysmlc_rosetta.targets import TARGETS

if TYPE_CHECKING:
    from sysmlc_rosetta.program import (
        LfProgram,
        LogicalAction,
        MainReactor,
        Mode,
        Reaction,
        Reactor,
    )

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
    """Serialize an ``LfProgram`` to Lingua Franca source text.

    Child reactor classes render before the machine reactor (lfc requires
    definition before use); the trivial ``main`` instantiates the machine
    (the last reactor).
    """
    lines: list[str] = [
        *_target_lines(TARGETS[program.target].lf_name, program.target_options),
        "",
    ]
    if program.preamble:
        lines.append("preamble {=")
        lines.extend(f"{_INDENT}{line}" for line in program.preamble)
        lines.extend(("=}", ""))
    for reactor in program.reactors:
        lines.extend(_reactor_lines(reactor))
        lines.append("")
    lines.extend(_main_lines(program))
    return "\n".join(lines) + "\n"


def _target_lines(
    target: str, options: tuple[tuple[str, str], ...]
) -> list[str]:
    # No options -> bare ``target <Name>`` (byte-identical legacy output).
    if not options:
        return [f"target {target}"]
    lines = [f"target {target} {{"]
    lines.extend(f"{_INDENT}{key}: {value}," for key, value in options)
    lines.append("}")
    return lines


def _main_lines(program: LfProgram) -> list[str]:
    main = program.main
    if main is None:
        # Trivial main instantiates the last reactor (legacy fallback).
        return [
            "main reactor {",
            f"{_INDENT}m = new {program.reactor.name}()",
            "}",
        ]
    return _main_reactor_lines(main)


def _main_reactor_lines(main: MainReactor) -> list[str]:
    lines = ["main reactor {"]
    lines.extend(
        f"{_INDENT}{inst.name} = new {inst.reactor}()"
        for inst in main.instantiations
    )
    lines.extend(
        f"{_INDENT}{conn.source} -> {conn.target}" for conn in main.connections
    )
    for reaction in main.reactions:
        lines.extend(_reaction_lines(reaction, depth=1))
    lines.append("}")
    return lines


def _reactor_lines(reactor: Reactor) -> list[str]:
    # Parameter defaults and state initializers are wrapped in {= ... =}
    # unconditionally: lfc parses bare initializers as LF values and rejects
    # any non-literal Python (e.g. Point(x=0.5), LightColor.red).
    params = ", ".join(
        f"{p.name}{_typed(p.type)} = {{= {p.default} =}}"
        for p in reactor.parameters
    )
    header = (
        f"reactor {reactor.name}({params})"
        if params
        else f"reactor {reactor.name}"
    )
    lines = [f"{header} {{"]
    ports = dict(reactor.port_types)
    lines.extend(
        f"{_INDENT}input {name}{_typed(ports.get(name))}"
        for name in reactor.inputs
    )
    lines.extend(
        f"{_INDENT}output {name}{_typed(ports.get(name))}"
        for name in reactor.outputs
    )
    lines.extend(
        f"{_INDENT}{'reset ' if var.reset else ''}"
        f"state {var.name}{_typed(var.type)} = {{= {var.init} =}}"
        for var in reactor.state_vars
    )
    lines.extend(_action_line(_INDENT, action) for action in reactor.actions)
    lines.extend(
        f"{_INDENT}{inst.name} = new {inst.reactor}()"
        for inst in reactor.instantiations
    )
    lines.extend(
        f"{_INDENT}{conn.source} -> {conn.target}"
        for conn in reactor.connections
    )
    for reaction in reactor.reactions:
        lines.extend(_reaction_lines(reaction, depth=1))
    for mode in reactor.modes:
        lines.extend(_mode_lines(mode))
    for reaction in reactor.trailing_reactions:
        lines.extend(_reaction_lines(reaction, depth=1))
    lines.append("}")
    return lines


def _mode_lines(mode: Mode) -> list[str]:
    keyword = "initial mode" if mode.initial else "mode"
    lines = [f"{_INDENT}{keyword} {mode.name} {{"]
    lines.extend(
        f"{_INDENT * 2}timer {timer.name}({timer.offset})"
        for timer in mode.timers
    )
    lines.extend(_action_line(_INDENT * 2, action) for action in mode.actions)
    lines.extend(
        f"{_INDENT * 2}{inst.name} = new {inst.reactor}()"
        for inst in mode.instantiations
    )
    lines.extend(
        f"{_INDENT * 2}{conn.source} -> {conn.target}"
        for conn in mode.connections
    )
    for reaction in mode.reactions:
        lines.extend(_reaction_lines(reaction, depth=2))
    lines.append(f"{_INDENT}}}")
    return lines


def _reaction_lines(reaction: Reaction, depth: int) -> list[str]:
    pad = _INDENT * depth
    sources = f" {', '.join(reaction.sources)}" if reaction.sources else ""
    effects = f" -> {', '.join(reaction.effects)}" if reaction.effects else ""
    lines = [
        f"{pad}reaction({', '.join(reaction.triggers)}){sources}{effects} {{="
    ]
    lines.extend(f"{pad}{_INDENT}{line}" for line in reaction.body)
    lines.append(f"{pad}=}}")
    return lines


def _typed(ctype: str | None) -> str:
    """A declaration's ``: <type>`` suffix; nothing for an untyped one.

    A type LF cannot parse as an identifier (``const char*``) is target
    code: ``{= ... =}``.
    """
    if ctype is None:
        return ""
    return f": {ctype}" if ctype.isidentifier() else f": {{= {ctype} =}}"


def _action_line(pad: str, action: LogicalAction) -> str:
    return f"{pad}logical action {action.name}{_typed(action.type)}"
