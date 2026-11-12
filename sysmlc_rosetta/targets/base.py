"""The vocabulary every rosetta target language provides.

The builder never spells target code itself. It asks an :class:`LfTarget`
for each statement and condition, by meaning, and the target renders the
resulting statement tree into lines of its language.

Names used throughout:

- *state variable*: a reactor ``state`` the builder declared, by bare name;
- *port*: a rendered LF port reference (``Sig``, ``c_x._busy``);
- *occurrence*: an expression for the arrival tag of the event that
  selected a transition, or :meth:`LfTarget.continuation` for an eventless
  one;
- *pending slot*: the variable retaining one trigger of a composite state
  while its children settle; it is *closed* (the state's activation
  ended), *open* (active, nothing retained) or *holding* ``(key, payload,
  tag)``;
- *selection slot*: the variable recording a draining state's departure;
  it is *idle*, *ancestor* (an enclosing state asked to exit) or
  *selected* ``(key, payload)``;
- *consumption flag*: an output telling enclosing scopes whether a signal
  occurrence was *accepted* (with its occurrence), *held* for settlement or
  *declined*.

Fixed locals a body may bind: ``_tag``, ``_value``, ``_newest``, ``_key``,
``_held``, ``_after_ns`` and ``_at_delta``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Final, Literal

from sysmlc_rosetta.statements import nest

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import syside
    from sysmlc.semantics.statemachine.facts import AttributeBinding
    from sysmlc.sysml.foreign_artifact.base import ForeignArtifact

    from sysmlc_rosetta.codegen import PreambleNeeds
    from sysmlc_rosetta.program import LfProgram, Reactor, StateVar
    from sysmlc_rosetta.statements import Body, Node, Stmt

#: What a protocol state variable declared by :meth:`LfTarget.state_vars`
#: holds.
type StateKind = Literal[
    "flag", "due", "since", "pending", "selection", "active", "busy", "wait"
]

#: The active mode of a reporting scope during its selected cleanup.
CLEANUP: Final = ""


def files_option(
    companion: str | None, external: list[ForeignArtifact] | None
) -> tuple[str, str] | None:
    """Build the ``files:`` target option, or None when nothing to ship.

    Args:
        companion: The companion types file's name, if any.
        external: The foreign artifacts shipped beside the program.
    """
    names: list[str] = []
    if companion is not None:
        names.append(companion)
    if external:
        for artifact in external:
            names.append(artifact.path.name)
    if not names:
        return None
    listed = ", ".join(f'"{n}"' for n in names)
    return ("files", f"[{listed}]")


class LfTarget(ABC):
    """A Lingua Franca target language for generated reaction bodies.

    Attributes:
        name: The target's name, as ``--target`` selects it.
        lf_name: The LF ``target`` keyword.
        foreign_languages: The foreign-artifact languages the target
            calls into.
        extension: The extension of the program's source file.
        mode_local_children: Whether only a mode's own reactions may refer
            to the reactors the mode contains.
    """

    name: ClassVar[str]
    lf_name: ClassVar[str]
    foreign_languages: ClassVar[tuple[str, ...]]
    extension: ClassVar[str] = "lf"
    mode_local_children: ClassVar[bool] = False

    # -- program --

    @abstractmethod
    def register_definition(
        self,
        definition: syside.Definition,
        needs: PreambleNeeds,
        render_default: Callable[[syside.Expression], str],
    ) -> None:
        """Make a payload or structured attribute type available."""

    @abstractmethod
    def register_signal(
        self, channel: str, definition: syside.Definition | None
    ) -> None:
        """Record the payload definition a signal channel carries."""

    def attribute_type(
        self, binding: AttributeBinding
    ) -> tuple[str, str] | None:
        """A bound attribute's declared type and its default initializer.

        The initializer is the one an attribute without a value gets. An
        untyped language declares neither: None.
        """
        return None

    def declare(self, reactor: Reactor) -> Reactor:
        """Complete a built reactor's declarations for the language."""
        return reactor

    @abstractmethod
    def preamble_lines(self, needs: PreambleNeeds) -> list[str]:
        """The program's file-level preamble."""

    @abstractmethod
    def target_options(
        self, needs: PreambleNeeds
    ) -> tuple[tuple[str, str], ...]:
        """Target header options the program's companion files need."""

    @abstractmethod
    def support_files(
        self, needs: PreambleNeeds
    ) -> tuple[tuple[str, str], ...]:
        """Files the program ships beside its ``.lf``: ``(name, text)``."""

    # -- program text --

    def header(self, options: tuple[tuple[str, str], ...]) -> list[str]:
        """The program's ``target`` declaration, carrying ``options``.

        Without options, it is a bare ``target <Name>`` line.
        """
        if not options:
            return [f"target {self.lf_name}"]
        return [
            f"target {self.lf_name} {{",
            *(f"  {key}: {value}," for key, value in options),
            "}",
        ]

    def main_opening(self, program: LfProgram) -> list[str]:
        """The lines opening ``program``'s main reactor, before its body."""
        return ["main reactor {"]

    def adapt(self, reactor: Reactor) -> Reactor:
        """A finished reactor in the shape the target's compiler accepts.

        Serialization applies it last, after any caller edited the program.
        """
        return reactor

    # -- declarations --

    @abstractmethod
    def state_vars(
        self,
        name: str,
        kind: StateKind,
        *,
        reset: bool,
        insts: Sequence[str] = (),
    ) -> tuple[StateVar, ...]:
        """Declare a protocol state variable.

        Args:
            name: The variable; for ``busy``, the source state's simple name.
            kind: What the variable holds.
            reset: Whether it is a ``reset state``.
            insts: For ``busy``, the state's child instances; for ``wait``,
                the earlier regions' instances.
        """

    # -- harness observation --

    @abstractmethod
    def observe_entry(self, path: str) -> Stmt:
        """Print ``__SYSMLC_ENTRY__ <elapsed ms> <path>`` on stdout."""

    @abstractmethod
    def observe_value(self, attribute: str, ctype: str | None) -> Stmt:
        """Print ``__SYSMLC_VALUE__ <attribute> <JSON value>`` on stdout."""

    # -- rendering --

    @abstractmethod
    def render(self, body: Body) -> tuple[str, ...]:
        """Render a statement tree as unindented lines of the language."""

    # -- values and conditions --

    @abstractmethod
    def boolean(self, value: bool) -> str:
        """A boolean literal."""

    @abstractmethod
    def var(self, name: str) -> str:
        """A reference to a state variable."""

    @abstractmethod
    def is_present(self, port: str) -> str:
        """Whether ``port`` (or an action) is present at this tag."""

    @abstractmethod
    def value(self, port: str) -> str:
        """The value of a present port or action."""

    @abstractmethod
    def value_or(self, port: str, fallback: str) -> str:
        """The value of ``port`` when present, else ``fallback``."""

    @abstractmethod
    def no_payload(self) -> str:
        """The payload of a trigger that carries none."""

    @abstractmethod
    def negate(self, condition: str) -> str:
        """``condition`` negated; ``condition`` is wrapped as needed."""

    @abstractmethod
    def not_var(self, name: str) -> str:
        """Whether a boolean state variable is false."""

    @abstractmethod
    def all_of(self, conditions: Sequence[str]) -> str:
        """Whether every condition holds; callers group compound operands."""

    @abstractmethod
    def any_of(self, conditions: Sequence[str]) -> str:
        """Whether some condition holds; callers group compound operands."""

    def grouped(self, condition: str) -> str:
        """``condition`` in parentheses."""
        return f"({condition})"

    # -- plain statements --

    @abstractmethod
    def set_flag(self, name: str, value: bool) -> Stmt:
        """Set a boolean state variable."""

    @abstractmethod
    def set_port(self, port: str, value: str) -> Stmt:
        """Set an output port (or a contained reactor's input)."""

    @abstractmethod
    def set_mode(self, mode: str) -> Stmt:
        """Switch to ``mode`` with reset at the next microstep."""

    @abstractmethod
    def schedule(
        self, action: str, delay: str = "0", value: str | None = None
    ) -> Stmt:
        """Schedule a logical action, optionally carrying ``value``."""

    @abstractmethod
    def fail(self, message: str) -> Stmt:
        """Stop the program with a runtime error."""

    @abstractmethod
    def request_stop(self) -> Stmt:
        """Request the program to stop at the end of this tag."""

    @abstractmethod
    def log(self, message: str) -> Stmt:
        """Emit a debug-level log line."""

    @abstractmethod
    def check(self, condition: str, message: str) -> Node:
        """Fail with ``message`` unless ``condition`` holds."""

    def write_event(self, port: str, value: str | None) -> Body:
        """Write one modeled event, refusing a second one at this tag.

        A ``value`` of None sends a bare signal.
        """
        message = (
            f"multiple modeled events on LF port {port} at the same tag; "
            "event coalescing is not supported"
        )
        return (
            nest(self.is_present(port), (self.fail(message),)),
            self.set_port(port, value or self.boolean(True)),
        )

    # -- current state announcements --

    @abstractmethod
    def announce(self, path: str, scope_path: str) -> Stmt:
        """Announce that the scope entered state ``path``.

        Args:
            path: The state's name within its scope.
            scope_path: The scope's dotted path within the machine.
        """

    @abstractmethod
    def reemit(self, inst: str, prefix: str) -> Stmt:
        """Forward a child's announcement upward under ``prefix``."""

    # -- time --

    @abstractmethod
    def arrival(self) -> str:
        """The current tag, as an occurrence."""

    @abstractmethod
    def continuation(self) -> str:
        """The occurrence of an eventless continuation."""

    @abstractmethod
    def seconds_to_ns(self, seconds: str) -> str:
        """Seconds (a real expression) as an LF interval."""

    @abstractmethod
    def bind_after(self, delay_ns: str) -> Stmt:
        """Bind the local ``_after_ns`` to an interval."""

    @abstractmethod
    def bind_at_delta(self, instant_ns: str) -> Stmt:
        """Bind the local ``_at_delta`` to ``instant`` minus elapsed time."""

    def at_delta_reached(self) -> str:
        """Whether ``_at_delta`` is not in the past."""
        return "_at_delta >= 0"

    @abstractmethod
    def arm_due(self, due: str, delay_ns: str) -> Stmt:
        """Record the elapsed time a timer armed now fires at."""

    @abstractmethod
    def arm_at_due(self, due: str) -> Stmt:
        """Record an ``at`` trigger's due time, or none if it is past."""

    @abstractmethod
    def due_reached(self, due: str) -> str:
        """Whether this callback is the one the activation armed."""

    @abstractmethod
    def clear_due(self, due: str) -> Stmt:
        """Consume a due-time record."""

    @abstractmethod
    def mark_since(self, since: str) -> Stmt:
        """Record when the scope's state was entered."""

    @abstractmethod
    def arrived_since(self, occurrence: str, since: str) -> str:
        """Whether ``occurrence`` arrived after the state was entered."""

    # -- children busy tracking --

    @abstractmethod
    def busy_init(self, state: str, insts: Sequence[str]) -> Body:
        """Mark every child of ``state`` busy (they are entering)."""

    @abstractmethod
    def busy_add(self, state: str, inst: str) -> Stmt:
        """Mark a child busy."""

    @abstractmethod
    def busy_discard(self, state: str, inst: str) -> Stmt:
        """Mark a child settled."""

    @abstractmethod
    def busy_any(self, state: str, insts: Sequence[str]) -> str:
        """Whether some child of ``state`` is still marked busy."""

    @abstractmethod
    def busy_none(self, state: str, insts: Sequence[str]) -> str:
        """Whether no child of ``state`` is marked busy."""

    @abstractmethod
    def report_busy(self, occurrence: str) -> Stmt:
        """Report busy upward, keeping the latest occurrence at this tag."""

    @abstractmethod
    def bind_value(self, value: str, signal: str) -> Body:
        """Bind the local ``_value`` to a ``signal`` payload."""

    @abstractmethod
    def bind_tag(self, occurrence: str) -> Stmt:
        """Bind the local ``_tag`` to an occurrence."""

    @abstractmethod
    def bind_newest(self, insts: Sequence[str]) -> Body:
        """Bind ``_newest`` to the latest occurrence children report now."""

    def newest(self) -> str:
        """The local ``_newest``."""
        return "_newest"

    def tag(self) -> str:
        """The local ``_tag``."""
        return "_tag"

    # -- pending slot --

    @abstractmethod
    def pending_holding(self, pending: str) -> str:
        """Whether the pending slot holds a trigger."""

    @abstractmethod
    def pending_closed(self, pending: str) -> str:
        """Whether the pending slot is closed."""

    @abstractmethod
    def pending_not_closed(self, pending: str) -> str:
        """Whether the pending slot is open or holding."""

    @abstractmethod
    def pending_open(self, pending: str) -> Stmt:
        """Open the pending slot, holding nothing."""

    @abstractmethod
    def pending_close(self, pending: str) -> Stmt:
        """Close the pending slot."""

    @abstractmethod
    def pending_hold(
        self, pending: str, key: int, payload: str, signal: str | None
    ) -> Stmt:
        """Hold ``(key, payload, _tag)`` in the pending slot.

        ``signal`` names the payload's channel, or is None without payload.
        """

    @abstractmethod
    def pending_tag(self, pending: str) -> str:
        """The held trigger's arrival tag."""

    @abstractmethod
    def pending_later_than(self, pending: str, occurrence: str) -> str:
        """Whether ``occurrence`` is later than the held trigger."""

    @abstractmethod
    def pending_key_is(self, pending: str, key: int) -> str:
        """Whether the slot holds the trigger ``key`` (slot is holding)."""

    @abstractmethod
    def pending_take(self, pending: str) -> Body:
        """Bind ``_key``, the payload and ``_tag`` from the held trigger."""

    def taken_key_is(self, key: int) -> str:
        """Whether the taken (or selected) key equals ``key``."""
        return f"_key == {key}"

    @abstractmethod
    def bind_held(self, pending: str) -> Stmt:
        """Bind ``_held`` to the held key, or to nothing."""

    def held_is(self, key: int) -> str:
        """Whether ``_held`` equals ``key``."""
        return f"_held == {key}"

    # -- selection slot --

    @abstractmethod
    def sel_idle(self, sel: str) -> Stmt:
        """Make the selection slot idle."""

    @abstractmethod
    def sel_ancestor(self, sel: str) -> Stmt:
        """Record an exit requested by an enclosing state."""

    @abstractmethod
    def sel_select(
        self, sel: str, key: int, payload: str, signal: str | None
    ) -> Stmt:
        """Record a selected departure ``(key, payload)`` of ``signal``."""

    @abstractmethod
    def unpack_value(self, channels: Sequence[str]) -> Body:
        """Bind ``_value`` to a taken or selected payload.

        Args:
            channels: The channels the payload may have arrived on.
        """

    @abstractmethod
    def sel_busy(self, sel: str) -> str:
        """Whether the selection slot is not idle."""

    @abstractmethod
    def sel_selected(self, sel: str) -> str:
        """Whether a departure was selected (not idle, not ancestor)."""

    @abstractmethod
    def sel_take(self, sel: str) -> Body:
        """Bind ``_key`` and the payload from the selected departure."""

    # -- active mode of a reporting scope --

    @abstractmethod
    def active_is(self, mode: str) -> str:
        """Whether ``mode`` is the scope's active mode."""

    @abstractmethod
    def active_is_none(self) -> str:
        """Whether the scope has no active mode (finalized or exited)."""

    @abstractmethod
    def set_active(self, mode: str | None) -> Stmt:
        """Set the active mode: a mode, :data:`CLEANUP`, or None (exited)."""

    # -- consumption flags --

    @abstractmethod
    def consume(self, flag: str, outcome: str) -> Stmt:
        """Set a consumption flag to an occurrence or a fixed outcome.

        Args:
            flag: The flag port.
            outcome: An occurrence expression, or one of
                :meth:`accepted`, :meth:`held`, :meth:`declined`.
        """

    @abstractmethod
    def accepted(self) -> str:
        """The outcome of an occurrence accepted outside a reporting scope."""

    @abstractmethod
    def held(self) -> str:
        """The outcome of an occurrence retained for settlement."""

    @abstractmethod
    def declined(self) -> str:
        """The outcome of a retained occurrence that was not accepted."""

    @abstractmethod
    def consumed_at(self, flag: str, occurrence: str) -> str:
        """Whether ``flag`` reports ``occurrence`` accepted."""

    @abstractmethod
    def consumption_decided(self, flag: str) -> str:
        """Whether ``flag`` reports an acceptance (not held or declined)."""

    @abstractmethod
    def forward_consumption(
        self, flag: str, source: str, pending: str | None, keys: Sequence[int]
    ) -> Body:
        """Forward a child's consumption flag upward.

        While ``pending`` holds one of ``keys``, a decline is forwarded as
        held instead.
        """

    # -- parallel regions sharing a signal --

    @abstractmethod
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
        """Forward one shared occurrence to a later region.

        The gate holds the occurrence until every earlier region passed on
        it; it retires the occurrence once one accepted it.
        """

    @abstractmethod
    def clear_wait(self, wait: str) -> Stmt:
        """Clear a region gate's held occurrence."""
