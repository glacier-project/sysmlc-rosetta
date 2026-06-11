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
    """One LF mode: the translation of a SysML leaf state."""

    name: str
    initial: bool = False
    timers: tuple[Timer, ...] = ()
    actions: tuple[LogicalAction, ...] = ()
    reactions: tuple[Reaction, ...] = ()


@dataclass(frozen=True)
class Reactor:
    """The machine reactor: a flat SysML state definition, translated."""

    name: str
    parameters: tuple[Parameter, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    state_vars: tuple[StateVar, ...] = ()
    actions: tuple[LogicalAction, ...] = ()
    reactions: tuple[Reaction, ...] = ()
    modes: tuple[Mode, ...] = ()


@dataclass(frozen=True)
class LfProgram:
    """A Lingua Franca program: one machine reactor plus a trivial main.

    ``preamble`` holds Python preamble lines (imports/helpers); empty means
    no preamble block is emitted.
    """

    reactor: Reactor
    preamble: tuple[str, ...] = ()
