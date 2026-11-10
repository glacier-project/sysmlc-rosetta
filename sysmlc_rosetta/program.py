from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Parameter:
    """A reactor parameter with its rendered default.

    ``type`` is its C type; None on the Python target.
    """

    name: str
    default: str
    type: str | None = None


@dataclass(frozen=True)
class StateVar:
    """A reactor state variable with its rendered initializer.

    ``reset`` marks the variable for automatic reset on mode entry (LF
    ``reset state``).  Join-completion flags must be reset so lfc accepts
    their parent reactor when it is instantiated inside a ``reset`` mode.
    ``type`` is its C type; None on the Python target.
    """

    name: str
    init: str
    reset: bool = False
    type: str | None = None


@dataclass(frozen=True)
class Timer:
    """A mode-local timer; ``offset`` is a rendered LF duration ("5 sec")."""

    name: str
    offset: str


@dataclass(frozen=True)
class LogicalAction:
    """A named logical action (reactor- or mode-level).

    ``type`` is the C type of the value it carries, if any.
    """

    name: str
    type: str | None = None


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
    """One LF reaction: triggers, effects, body and sources.

    ``triggers``, ``effects`` and ``sources`` are rendered LF identifiers
    (``"startup"``, ``"Tick"``, ``"reset(running)"``); ``body`` holds
    unindented lines of the target language, including nested indentation.
    ``sources`` are non-triggering port dependencies. While rosetta builds
    a program, ``body`` holds a statement tree instead (see
    :func:`sysmlc_rosetta.statements.stored`).
    """

    triggers: tuple[str, ...]
    effects: tuple[str, ...] = ()
    body: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


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
    ``trailing_reactions`` are reactor-level reactions emitted after all
    modes, so they follow every mode reaction in LF's reaction order.
    ``port_types`` gives each input and output its C type; empty on the
    Python target, whose ports are untyped. Ports are names elsewhere in the
    program, so their types sit beside them rather than on a port object.
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
    trailing_reactions: tuple[Reaction, ...] = ()
    port_types: tuple[tuple[str, str], ...] = ()


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
class LfConstraint:
    """Source identity of an assertion in a generated reactor.

    Attributes:
        reactor: Generated reactor class containing the assertion.
        check_id: Constraint ordinal within the compiled behavior.
        scope: Neutral SysML state path, empty for the behavior root.
        name: Declared constraint name, or ``None`` for an anonymous check.
        behavior: Qualified name of the original SysML state definition.
    """

    reactor: str
    check_id: int
    scope: str
    name: str | None
    behavior: str


@dataclass(frozen=True)
class LfProgram:
    """A Lingua Franca program: reactor classes plus a main.

    ``reactors`` holds child reactor classes first and the machine reactor
    last (lfc wants definitions before use). When ``main`` is None the trivial
    main instantiates the last reactor; otherwise the explicit ``MainReactor``
    is rendered. ``preamble`` holds the target language's preamble lines;
    empty means no preamble block is emitted. ``target_options`` populate the
    ``target <Name> { ... }`` header (run config); empty -> a bare ``target``
    line. ``types_module_name`` and ``types_module_lines`` describe the
    Python target's companion ``<basename>_types.py`` module.
    ``constraints`` preserves source identities carried by generated
    assertion diagnostics. ``target`` is the LF target language, and
    ``support_files`` the C target's companion files as ``(file name,
    content)``.
    """

    reactors: tuple[Reactor, ...]
    preamble: tuple[str, ...] = ()
    main: MainReactor | None = None
    target_options: tuple[tuple[str, str], ...] = ()
    types_module_name: str | None = None
    types_module_lines: tuple[str, ...] = ()
    constraints: tuple[LfConstraint, ...] = ()
    signal_channels: tuple[tuple[str, str], ...] = ()
    signal_aliases: tuple[tuple[str, str], ...] = ()
    target: str = "python"
    support_files: tuple[tuple[str, str], ...] = ()

    def resolve_signal(self, name: str) -> str:
        """Resolve a qualified input name or an unambiguous simple name.

        Raises:
            ValueError: If the name is unknown or ambiguous.
        """
        channels = dict(self.signal_channels)
        if name in channels:
            return channels[name]
        aliases = dict(self.signal_aliases)
        if name not in aliases:
            raise ValueError(
                f"signal {name!r} is unknown or ambiguous; "
                "use a qualified type name"
            )
        return aliases[name]

    @property
    def reactor(self) -> Reactor:
        """The machine reactor (always last; children precede it)."""
        return self.reactors[-1]
