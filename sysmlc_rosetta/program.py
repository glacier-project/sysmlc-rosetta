from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Parameter:
    """A reactor parameter with its rendered Python default."""

    name: str
    default: str


@dataclass(frozen=True)
class StateVar:
    """A reactor state variable with its rendered Python initializer."""

    name: str
    init: str


@dataclass(frozen=True)
class Timer:
    """A mode-local timer; ``offset`` is a rendered LF duration ("5 sec")."""

    name: str
    offset: str


@dataclass(frozen=True)
class LogicalAction:
    """A named logical action (reactor- or mode-level)."""

    name: str


@dataclass(frozen=True)
class Instantiation:
    """A contained reactor instance: ``c_x = new Machine_x()``."""

    name: str
    reactor: str


@dataclass(frozen=True)
class Connection:
    """One connection statement; endpoints are rendered LF port references."""

    source: str
    target: str


@dataclass(frozen=True)
class Reaction:
    """One LF reaction: trigger names, effect names, Python body lines.

    ``triggers`` and ``effects`` are rendered LF identifiers (``"startup"``,
    ``"Tick"``, ``"reset(running)"``); ``body`` holds unindented Python
    lines (nested Python indentation included in the line itself).
    """

    triggers: tuple[str, ...]
    effects: tuple[str, ...] = ()
    body: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mode:
    """One LF mode: the translation of a SysML state.

    A leaf state's mode carries only reactions/timers/actions; a composite
    (or parallel) state's mode additionally instantiates the child reactor(s)
    and connects forwarded inputs down.
    """

    name: str
    initial: bool = False
    timers: tuple[Timer, ...] = ()
    actions: tuple[LogicalAction, ...] = ()
    instantiations: tuple[Instantiation, ...] = ()
    connections: tuple[Connection, ...] = ()
    reactions: tuple[Reaction, ...] = ()


@dataclass(frozen=True)
class Reactor:
    """One reactor class: the machine itself or a composite-scope child.

    Reactor-level ``instantiations``/``connections`` are used by a parallel
    root machine, whose region instances live outside any mode.
    """

    name: str
    parameters: tuple[Parameter, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    state_vars: tuple[StateVar, ...] = ()
    actions: tuple[LogicalAction, ...] = ()
    instantiations: tuple[Instantiation, ...] = ()
    connections: tuple[Connection, ...] = ()
    reactions: tuple[Reaction, ...] = ()
    modes: tuple[Mode, ...] = ()


@dataclass(frozen=True)
class MainReactor:
    """An explicit ``main reactor`` body: a part system's composition.

    Like ``Reactor`` but with no ports or modes — it only instantiates the
    part reactors, wires their connected ports, and (optionally) carries
    part-level reactions. Used by the part assembler; a program with
    ``main=None`` falls back to the trivial ``m = new <last>()`` main.
    """

    instantiations: tuple[Instantiation, ...] = ()
    connections: tuple[Connection, ...] = ()
    reactions: tuple[Reaction, ...] = ()


@dataclass(frozen=True)
class LfProgram:
    """A Lingua Franca program: reactor classes plus a main.

    ``reactors`` holds child reactor classes first and the machine reactor
    last (lfc wants definitions before use). When ``main`` is None the trivial
    main instantiates the last reactor; otherwise the explicit ``MainReactor``
    is rendered. ``preamble`` holds Python preamble lines (imports/helpers);
    empty means no preamble block is emitted. ``target_options`` populate the
    ``target Python { ... }`` header (run config); empty -> bare ``target
    Python``.
    """

    reactors: tuple[Reactor, ...]
    preamble: tuple[str, ...] = ()
    main: MainReactor | None = None
    target_options: tuple[tuple[str, str], ...] = ()

    @property
    def reactor(self) -> Reactor:
        """The machine reactor (always last; children precede it)."""
        return self.reactors[-1]
