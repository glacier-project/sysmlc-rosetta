from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING

import syside
from sysmlc.codegen.python import payload_signature
from sysmlc.codegen.structured import (
    constructed_payload_definition,
    register_dataclass,
)
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.statemachine import actions, transitions
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.semantics.statemachine.facts import (
    AfterTrigger,
    AttributeBinding,
    AttributeDirection,
    AttributeValue,
    AtTrigger,
    CompletionTarget,
    ConstraintFact,
    SignalTrigger,
    StateFact,
    StateKind,
    TransitionFact,
    WhenTrigger,
)
from sysmlc.semantics.statemachine.interface import send_via_port
from sysmlc.semantics.statemachine.signals import SignalCatalog

from sysmlc_rosetta.codegen import (
    LfPythonCodeGen,
    PreambleNeeds,
    files_option,
)
from sysmlc_rosetta.lifecycle import (
    _ARRIVAL,
    _CONTINUATION,
    _PY_INDENT,
    ACTIVE_VAR,
    BUSY_PORT,
    ENTRY_ORDER_PORT,
    EXIT_ACK_PORT,
    EXIT_PENDING_VAR,
    EXIT_REQUEST_PORT,
    SETTLED_PORT,
    busy_condition,
    declining,
    exit_handler,
    lifecycle_reaction,
    make_reaction,
    overtaking,
    report_busy,
    retain,
    when_due,
)
from sysmlc_rosetta.program import (
    Connection,
    Instantiation,
    LfConstraint,
    LfProgram,
    LogicalAction,
    Mode,
    Parameter,
    Reaction,
    Reactor,
    StateVar,
    Timer,
)
from sysmlc_rosetta.serialize import render_duration

if TYPE_CHECKING:
    from sysmlc.sysml.foreign_artifact.base import ForeignArtifact

logger = logging.getLogger(__name__)

OUTPUT_PORT = "current_state"
COMPLETED_PORT = "completed"
DONE_MODE = "done"
CHANGE_ACT = "_change_act"


def _scope_of(path: str) -> str:
    """The enclosing scope of a state path (``""`` for top-level states)."""
    return path.rpartition("::")[0]


@dataclass
class _Trailing:
    """A reactor's reactor-level reactions emitted after all its modes.

    They are concatenated once per reactor as the exit-request handler,
    then every drain chain, every late exit resolution and every
    cleanup-dependent output forwarder, so each consumer follows the
    reactions whose requests can cause its input.
    """

    chains: list[Reaction] = field(default_factory=list)
    late: list[Reaction] = field(default_factory=list)
    forwarding: list[Reaction] = field(default_factory=list)


@dataclass(frozen=True)
class _Drain:
    """How a selected departure of a draining state is deferred.

    Attributes:
        sel: State variable recording the selected transition key and
            payload for the continuation.
        request: The first child's exit-request port.
        keys: Transition identity (``id``) to its key within the source.
    """

    sel: str
    request: str
    keys: dict[int, int]


def _simple(path: str) -> str:
    """The last segment of a state path: the state's simple name."""
    return path.rpartition("::")[2]


def _is_ancestor(outer: str, inner: str) -> bool:
    """Whether scope ``outer`` strictly encloses scope ``inner``."""
    if outer == inner:
        return False
    return outer == "" or inner.startswith(f"{outer}::")


def _enclosing(scope: str) -> list[str]:
    """A scope and all its ancestors, innermost first (ends with ``""``)."""
    out = [scope]
    while scope:
        scope = _scope_of(scope)
        out.append(scope)
    return out


class RosettaBuilder:
    """Assemble a Lingua Franca modal-reactor program from neutral facts.

    Implements the ``TargetBuilder`` protocol. Every LF-specific
    representational choice lives here. Each composite scope (the root state
    definition, every composite state, every parallel region) becomes one
    reactor class; its direct child states become modes. A composite child's
    mode instantiates the child's reactor and forwards accepted signals down;
    a parallel child's mode instantiates one reactor per region. ``then
    done`` is context-sensitive: the root runs its exit and requests stop
    (host shutdown alone never runs it), a child sets its
    ``completed`` output, and an eventless transition out of a composite
    fires on that port (completion semantics). Transitions escaping a scope
    raise dedicated ``exit_<k>`` outputs that enclosing scopes resolve or
    re-raise. Non-trigger ``_entry_order`` dependencies order parent
    entry/do before child entry at the same tag. Below a composite/parallel
    state with a triggered transition, child reactors report ``_busy`` when
    they select a transition and ``_settled`` once target entry, nested
    entry, eventless closure and natural region finalization are done; a
    trigger of that state arriving while its subtree is busy is retained
    with its payload and dispatched on settlement. At most one occurrence
    is retained per state: a second arrival, or a later event accepted
    below the state while one is retained, stops execution with a runtime
    error instead of reordering or dropping events. Capability rejections (deep
    entry, cross-scope sends, non-inline ``do`` bodies,
    unstable self-loops, name collisions) also live here.

    In ``peer_accepts`` mode (the named signals are accepted by a peer
    machine), a matching send becomes an LF output port that the sending
    scope's reactor raises and enclosing scopes re-emit upward; the bare
    default (``peer_accepts=frozenset()``) leaves every code path untouched.
    """

    def __init__(
        self,
        name: str,
        *,
        peer_accepts: frozenset[str] = frozenset(),
        needs: PreambleNeeds | None = None,
        observe: bool = False,
        source_qn: str | None = None,
    ) -> None:
        """Initialize the builder.

        Args:
            name: The reactor name (the state definition's name).
            peer_accepts: Signals a peer machine accepts; a send of one of
                these becomes an LF output port instead of a self-event.
            needs: A shared preamble registry, or ``None`` for a fresh one.
            observe: When True, inject ``logging.debug`` on each state's
                entry and exit (opt-in; the part assembler sets it). Off by
                default, so existing build paths emit byte-identical output.
            source_qn: Qualified name of the original SysML state definition.
                Defaults to the reactor name for manually supplied facts.
        """
        self._name = name
        self._source_qn = source_qn if source_qn is not None else name
        self._peer_accepts = peer_accepts
        self._observe = observe
        self._constraints: list[ConstraintFact] = []
        self._needs = needs if needs is not None else PreambleNeeds()
        self._finalize_types = self._needs.types_module is None
        if self._finalize_types:
            self._needs.types_module = f"{name}_types"
        self._init_codegen = LfPythonCodeGen(
            frozenset(), needs=self._needs, self_prefix=False
        )
        # Re-created in result() once the bound attribute names are known.
        self._codegen = LfPythonCodeGen(frozenset(), needs=self._needs)
        self._attributes: list[AttributeBinding] = []
        self._attribute_names: frozenset[str] = frozenset()
        self._has_when: bool = False
        self._root: StateFact | None = None
        self._facts: dict[str, StateFact] = {}
        self._transitions: list[TransitionFact] = []
        # Populated by result() before assembly:
        self._children: dict[str, list[StateFact]] = {}
        self._scope_transitions: dict[str, list[TransitionFact]] = {}
        self._accepted: dict[str, dict[str, None]] = {}
        self._handled: dict[str, set[str]] = {}
        self._sent_by_scope: dict[str, dict[str, None]] = {}
        # scope -> signals with a self-directed (no-`via`) send in that
        #   scope; exactly the signals whose `<sig>_act` render_send
        #   schedules.
        self._self_sched_by_scope: dict[str, set[str]] = {}
        # scope -> signals whose {sig}_consumed flag this scope's reactor
        #   outputs (set by an inner transition here, or re-emitted from a
        #   child). Empty unless a cross-level priority conflict exists.
        self._consumed: dict[str, dict[str, None]] = {}
        # group-interrupt composite/parallel state -> signal -> boundary child
        #   scope names whose {sig}_consumed flag the group interrupt reads.
        self._guarded_interrupts: dict[str, dict[str, list[str]]] = {}
        self._shared: dict[str, dict[str, list[str]]] = {}
        self._origin: set[tuple[str, str]] = set()
        self._reentered: set[str] = set()
        self._tracked: set[str] = set()
        self._escaping: dict[str, dict[str, int]] = {}
        # Peer-mode state (empty unless peer_accepts is non-empty):
        # scope -> signals that scope's reactor must output (its own
        # peer-accepted sends plus those of its descendants).
        self._exported: dict[str, dict[str, None]] = {}
        # _port_sigs: machine-level sends that become output ports
        #   (root exports).
        self._port_sigs: frozenset[str] = frozenset()
        # _omitted_inputs: signals dropped from input ports; deliberately
        #   identity-equal to _port_sigs today (named separately because
        #   use sites read "interface rule" vs "codegen rule").
        self._omitted_inputs: frozenset[str] = frozenset()
        self._root_exit: list[str] = []
        # Populated during assembly:
        self._exit_ports: dict[str, dict[str, str]] = {}
        self._deferred: set[tuple[str, str]] = set()
        self._needs_done: set[str] = set()
        self._reactors: list[Reactor] = []

    # -- TargetBuilder protocol --

    def bind_attribute(self, binding: AttributeBinding) -> None:
        """Buffer a root-scope attribute (state-scoped ones are rejected)."""
        if binding.scope:
            raise UnsupportedConstructError(
                f"attribute {binding.name!r} is scoped to state "
                f"{binding.scope!r}; rosetta supports root-scope attributes "
                "only."
            )
        self._attributes.append(binding)

    def bind_constraint(self, fact: ConstraintFact) -> None:
        """Buffer a root-scope asserted constraint (scoped ones rejected).

        Substate reactors cannot see the machine's attributes, so a
        constraint scoped to a state has nothing meaningful to check there.
        """
        if fact.scope:
            raise UnsupportedConstructError(
                f"constraint {fact.name or '<anonymous>'!r} is scoped to "
                f"state {fact.scope!r}; rosetta checks root-scope "
                "constraints only."
            )
        self._constraints.append(fact)

    def add_state(self, state: StateFact) -> None:
        """Buffer a state fact; the whole tree is consumed in result()."""
        if state.parent is None:
            self._root = state
            return
        self._facts[state.name] = state

    def add_transition(self, transition: TransitionFact) -> None:
        """Buffer a transition fact (emitted in :meth:`result`)."""
        self._transitions.append(transition)

    def result(self) -> LfProgram:
        """Assemble and return the Lingua Franca program."""
        root = self._root
        if root is None:
            raise UnsupportedConstructError(
                "the state definition pushed no root state"
            )
        self._attribute_names = frozenset(
            binding.name for binding in self._attributes
        )
        self._has_when = any(
            isinstance(t.trigger, WhenTrigger) for t in self._transitions
        )
        for fact in self._facts.values():
            self._children.setdefault(_scope_of(fact.name), []).append(fact)
        self._classify_transitions()
        self._tracked = {
            t.source
            for t in self._transitions
            if t.trigger is not None
            and t.source in self._facts
            and self._facts[t.source].kind is not StateKind.LEAF
        }
        self._reentered = {
            t.target
            for t in self._transitions
            if isinstance(t.target, str)
            and (t.target == t.source or _is_ancestor(t.target, t.source))
        }
        for t in self._transitions:
            if not isinstance(t.target, str):
                continue
            levels: list[str] = []
            level = _scope_of(t.source)
            crosses_parallel = False
            while _is_ancestor(_scope_of(t.target), level):
                levels.append(level)
                parent = self._facts.get(_scope_of(level))
                if parent is not None and parent.kind is StateKind.PARALLEL:
                    targets = self._escaping.setdefault(parent.name, {})
                    targets.setdefault(t.target, len(targets))
                    crosses_parallel = True
                level = self._resolving_scope(level)
            if t.source in self._tracked or crosses_parallel:
                self._deferred.update((level, t.target) for level in levels)
        self._collect_signals()
        self._collect_consumed()
        self._collect_shared()
        self._collect_sends()
        # The port/self signal sets must exist (populated by _collect_sends)
        # before any peer-aware code generator is created.
        self._codegen = self._make_codegen(self._attribute_names)
        # Rendered even without a completion path so an unsupported root
        # exit is still diagnosed.
        self._root_exit = self._statements(root.exit_action, self._codegen)
        if root.kind is StateKind.PARALLEL:
            machine = self._parallel_root_reactor(root)
        elif root.kind is StateKind.COMPOSITE:
            machine = self._composite_reactor("", root, is_root=True)
        else:
            raise UnsupportedConstructError(
                "the state definition declares no substates."
            )
        self._reactors.append(
            self._with_constraint_checks(
                self._with_change_notifications(machine)
            )
        )
        program = LfProgram(
            reactors=tuple(self._reactors),
            preamble=tuple(self._needs.preamble_lines()),
            constraints=tuple(
                constraint
                for constraint in self._needs.constraints.values()
                if constraint.reactor == self._name
            ),
        )
        if self._finalize_types:
            program = finalize(program, self._needs)
        return program

    def _with_constraint_checks(self, machine: Reactor) -> Reactor:
        """Weave asserted-constraint checks into the machine reactor.

        Each root-scope ``assert constraint`` renders as a Python ``assert``
        placed (1) in a dedicated startup reaction — so a bad initial (or
        overridden) value aborts immediately — and (2) at the end of every
        reaction body that assigns to a bound attribute. The negated form
        ``assert not constraint`` asserts ``not (<expr>)``.
        """
        checks: list[str] = []
        for index, fact in enumerate(self._constraints):
            constraint = LfConstraint(
                self._name, index, fact.scope, fact.name, self._source_qn
            )
            self._needs.constraints[(self._name, index)] = constraint
            message = "SysML constraint violated: " + json.dumps(
                asdict(constraint), separators=(",", ":")
            )
            rendered = self._codegen.render_expression(fact.expression)
            if fact.is_negated:
                rendered = f"not ({rendered})"
            checks.append(f"assert {rendered}, {json.dumps(message)}")
        if not checks:
            return machine
        markers = tuple(f"self.{name} = " for name in self._attribute_names)

        def patched(reaction: Reaction) -> Reaction:
            mutates = any(
                marker in line for line in reaction.body for marker in markers
            )
            if not mutates:
                return reaction
            return replace(reaction, body=(*reaction.body, *checks))

        reactions = tuple(patched(r) for r in machine.reactions)
        reactions += (Reaction(("startup",), (), tuple(checks)),)
        modes = tuple(
            replace(mode, reactions=tuple(patched(r) for r in mode.reactions))
            for mode in machine.modes
        )
        trailing = tuple(patched(r) for r in machine.trailing_reactions)
        return replace(
            machine,
            reactions=reactions,
            modes=modes,
            trailing_reactions=trailing,
        )

    def _with_change_notifications(self, machine: Reactor) -> Reactor:
        """Schedule the change-notify action after each attribute assignment.

        ``accept when`` observations are re-checked one microstep after any
        root-scope attribute changes: a ``_change_act.schedule(0)`` is inserted
        immediately after every line that assigns a bound attribute, at that
        line's own indentation, so an assignment nested under a guard notifies
        only when it actually runs (a top-level notify would re-fire a
        ``_change_act``-triggered check reaction every activation and loop
        forever at frozen logical time). ``_change_act`` is declared an effect
        of any reaction that gains a notify. Emitted only when the machine has
        >=1 ``when`` trigger.
        """
        if not self._has_when:
            return machine
        markers = tuple(f"self.{name} = " for name in self._attribute_names)
        notify = f"{CHANGE_ACT}.schedule(0)"

        def patched(reaction: Reaction) -> Reaction:
            new_body: list[str] = []
            changed = False
            for line in reaction.body:
                new_body.append(line)
                if any(marker in line for marker in markers):
                    indent = line[: len(line) - len(line.lstrip())]
                    new_body.append(f"{indent}{notify}")
                    changed = True
            if not changed:
                return reaction
            return replace(
                reaction,
                effects=tuple(dict.fromkeys((*reaction.effects, CHANGE_ACT))),
                body=tuple(new_body),
            )

        reactions = tuple(patched(r) for r in machine.reactions)
        modes = tuple(
            replace(mode, reactions=tuple(patched(r) for r in mode.reactions))
            for mode in machine.modes
        )
        trailing = tuple(patched(r) for r in machine.trailing_reactions)
        return replace(
            machine,
            reactions=reactions,
            modes=modes,
            trailing_reactions=trailing,
        )

    # -- fact classification (runs before assembly) --

    def _classify_transitions(self) -> None:
        """Bucket transitions by source scope; reject deep entry.

        A transition may connect siblings, complete its own scope, or exit
        to a state in an enclosing scope. A target nested somewhere a
        sibling is not (deep entry) has no LF equivalent: entering a mode
        always activates the contained reactors' initial modes.
        """
        for t in self._transitions:
            src_scope = _scope_of(t.source)
            if isinstance(t.target, str):
                tgt_scope = _scope_of(t.target)
                if tgt_scope != src_scope and not _is_ancestor(
                    tgt_scope, src_scope
                ):
                    raise UnsupportedConstructError(
                        f"transition {t.source!r} -> {t.target!r} enters a "
                        "composite state from outside (deep entry); rosetta "
                        "supports sibling transitions and exits to enclosing "
                        "scopes only."
                    )
            self._scope_transitions.setdefault(src_scope, []).append(t)

    def _channels(self, trigger: SignalTrigger) -> tuple[str, ...]:
        """Resolve an accept to its concrete delivery channels."""
        if self._needs.catalog is None:
            return (trigger.signal_name,)
        return tuple(sorted(self._needs.catalog.matching_channels(trigger)))

    def _concrete_transitions(self) -> list[tuple[TransitionFact, str]]:
        """Expand channels while preserving original transition identities."""
        return [
            (transition, channel)
            for transition in self._transitions
            if isinstance(transition.trigger, SignalTrigger)
            for channel in self._channels(transition.trigger)
        ]

    def _collect_signals(self) -> None:
        """Map accepted signals to every scope that must port them.

        ``_handled[s]`` holds signals whose accepting transitions are
        reactions of scope ``s``'s reactor; ``_accepted[s]`` additionally
        includes signals accepted somewhere below ``s`` (the scope's input
        ports, forwarded down by connections).
        """
        for t, signal in self._concrete_transitions():
            trigger = t.trigger
            if not isinstance(trigger, SignalTrigger):
                continue
            if self._needs.catalog is not None:
                definition = self._needs.catalog.definition_for(signal)
                assert isinstance(definition, syside.Definition)
                self._register_dataclass(definition)
            src_scope = _scope_of(t.source)
            self._handled.setdefault(src_scope, set()).add(signal)
            for scope in _enclosing(src_scope):
                self._accepted.setdefault(scope, {}).setdefault(signal, None)

    def _collect_consumed(self) -> None:
        """Plan inner-first plumbing for cross-level priority conflicts.

        A group interrupt on a composite/parallel state ``C`` accepting signal
        ``s``, where ``s`` is also accepted strictly inside ``C``, must yield
        to the inner handler (UML/SCXML inner-first). ``C``'s group-interrupt
        reaction is guarded on a ``{s}_consumed`` flag the descendant raises
        and intermediate scopes re-emit upward, terminating at ``C`` (a
        composite) or at each consuming region (a parallel state).
        """
        for t, signal in self._concrete_transitions():
            trigger = t.trigger
            if not isinstance(trigger, SignalTrigger):
                continue
            composite = t.source
            fact = self._facts.get(composite)
            if fact is None or fact.kind is StateKind.LEAF:
                continue  # not a group interrupt on a composite/parallel state
            inner_scopes = [
                scope
                for scope, handled in self._handled.items()
                if signal in handled
                and (scope == composite or _is_ancestor(composite, scope))
            ]
            if not inner_scopes:
                continue
            parallel = fact.kind is StateKind.PARALLEL
            boundaries: list[str] = []
            for scope in inner_scopes:
                for enclosing in _enclosing(scope):
                    self._consumed.setdefault(enclosing, {}).setdefault(
                        signal, None
                    )
                    at_boundary = (
                        _scope_of(enclosing) == composite
                        if parallel
                        else enclosing == composite
                    )
                    if at_boundary:
                        if enclosing not in boundaries:
                            boundaries.append(enclosing)
                        break
            self._guarded_interrupts.setdefault(composite, {})[signal] = (
                boundaries
            )

    def _collect_shared(self) -> None:
        """Plan once-only acceptance of a signal shared by parallel regions.

        When two or more regions of one parallel state accept a signal,
        every accepting region but the last raises ``{s}_consumed`` (re-
        emitted up to the region), so a later region receives an
        occurrence only when no earlier region accepted it. Scopes of a
        later region receive the occurrence's arrival tag on an
        ``{s}_origin`` input, since an earlier region that holds the
        occurrence for settlement delays its delivery.
        """
        parallels = [
            f for f in self._facts.values() if f.kind is StateKind.PARALLEL
        ]
        root = self._root
        if root is not None and root.kind is StateKind.PARALLEL:
            parallels.append(root)
        for parallel in parallels:
            key = "" if parallel.parent is None else parallel.name
            accepting: dict[str, list[str]] = {}
            for region in self._children.get(key, []):
                for scope, handled in self._handled.items():
                    if scope != region.name and not _is_ancestor(
                        region.name, scope
                    ):
                        continue
                    for signal in handled:
                        names = accepting.setdefault(signal, [])
                        if region.name not in names:
                            names.append(region.name)
            for signal, names in accepting.items():
                if len(names) < 2:
                    continue
                self._shared.setdefault(key, {})[signal] = names
                for later in names[1:]:
                    self._origin |= {
                        (scope, signal)
                        for scope, accepted in self._accepted.items()
                        if signal in accepted
                        and (scope == later or _is_ancestor(later, scope))
                    }
                for scope, handled in self._handled.items():
                    region = next(
                        (
                            name
                            for name in names[:-1]
                            if scope == name or _is_ancestor(name, scope)
                        ),
                        None,
                    )
                    if region is None or signal not in handled:
                        continue
                    for enclosing in _enclosing(scope):
                        self._consumed.setdefault(enclosing, {}).setdefault(
                            signal, None
                        )
                        if enclosing == region:
                            break

    def _region_feed(
        self, key: str, region: StateFact, inst: str
    ) -> tuple[list[Connection], list[Reaction], list[StateVar]]:
        """Deliver a parallel state's signals to one region.

        A signal shared with earlier accepting regions passes through a
        gate holding the one occurrence: an earlier region's consumption
        reports it accepted (its arrival tag, or ``True``), held for
        settlement (``None``) or, after holding, declined (``False``). The
        gate forwards the original payload and arrival tag once every
        earlier region has passed on it, and retires it once one accepted.
        A second occurrence reaching a gate that still holds one stops
        execution, as for other overlapping retained events.

        Args:
            key: The parallel state's name, or ``""`` for a parallel root.
            region: The receiving region.
            inst: The region's reactor instance.
        """
        connections: list[Connection] = []
        reactions: list[Reaction] = []
        state: list[StateVar] = []
        scope = self._slot_scope(region)
        for sig in self._accepted.get(region.name, {}):
            if sig in self._omitted_inputs:
                continue
            names = self._shared.get(key, {}).get(sig, [])
            if region.name not in names[1:]:
                connections.append(Connection(sig, f"{inst}.{sig}"))
                if (region.name, sig) in self._origin:
                    origin = f"{sig}_origin"
                    connections.append(Connection(origin, f"{inst}.{origin}"))
                continue
            earlier = names[: names.index(region.name)]
            ports = ", ".join(
                f'("c_{_simple(name)}", c_{_simple(name)}.{sig}_consumed)'
                for name in earlier
            )
            wait = (
                f"self._{_simple(key) or 'root'}_{_simple(region.name)}"
                f"_{sig}_wait"
            )
            state.append(StateVar(wait[5:], "None", reset=scope != ""))
            arrival = self._arrival(scope, sig)
            message = (
                f"{self._source_qn}: signal {sig} arrived while an earlier "
                f"occurrence waits for region "
                f"{region.name.replace('::', '.')}; overlapping retained "
                "events are not supported"
            )
            body = (
                f"if {sig}.is_present:",
                f"{_PY_INDENT}if {wait} is not None:",
                f"{_PY_INDENT * 2}raise RuntimeError({json.dumps(message)})",
                f"{_PY_INDENT}{wait} = [{sig}.value, {arrival}, set()]",
                f"if {wait} is not None:",
                f"{_PY_INDENT}_value, _tag, _holders = {wait}",
                f"{_PY_INDENT}for _name, _port in ({ports},):",
                f"{_PY_INDENT * 2}if not _port.is_present:",
                f"{_PY_INDENT * 3}continue",
                f"{_PY_INDENT * 2}if _port.value is None:",
                f"{_PY_INDENT * 3}_holders.add(_name)",
                f"{_PY_INDENT * 2}elif _port.value is False:",
                f"{_PY_INDENT * 3}_holders.discard(_name)",
                f"{_PY_INDENT * 2}elif _port.value is True or "
                "_port.value == _tag:",
                f"{_PY_INDENT * 3}{wait} = None",
                f"{_PY_INDENT}if {wait} is not None and not _holders:",
                f"{_PY_INDENT * 2}{wait} = None",
                f"{_PY_INDENT * 2}{inst}.{sig}.set(_value)",
                f"{_PY_INDENT * 2}{inst}.{sig}_origin.set(_tag)",
            )
            triggers = (
                sig,
                *(f"c_{_simple(name)}.{sig}_consumed" for name in earlier),
            )
            sources = (f"{sig}_origin",) if (scope, sig) in self._origin else ()
            reactions.append(
                Reaction(
                    triggers,
                    (f"{inst}.{sig}", f"{inst}.{sig}_origin"),
                    body,
                    sources,
                )
            )
        return connections, reactions, state

    def _arrival(self, scope: str, sig: str) -> str:
        """The arrival tag of an occurrence of ``sig`` handled in ``scope``.

        A delayed shared occurrence carries its original arrival tag on
        the scope's ``{sig}_origin`` input; otherwise it arrives now.
        """
        if (scope, sig) not in self._origin:
            return _ARRIVAL
        return (
            f"({sig}_origin.value if {sig}_origin.is_present else {_ARRIVAL})"
        )

    def _slot_scope(self, fact: StateFact) -> str:
        """The scope whose reactor executes a state's own action slots.

        A parallel REGION's entry/do/exit run where the parallel state's
        mode lives (one scope further out); everything else runs in the
        scope that contains the state.
        """
        scope = _scope_of(fact.name)
        parent = self._facts.get(scope)
        if parent is not None and parent.kind is StateKind.PARALLEL:
            return _scope_of(parent.name)
        return scope

    def _collect_sends(self) -> None:
        """Collect sent signals per scope; reject cross-scope events.

        A signal sent in one scope but accepted in another would have to
        cross reactor boundaries, which rosetta does not route yet.
        """
        root = self._root
        assert root is not None
        slots: list[tuple[str, syside.ActionUsage | None]] = [
            ("", root.entry_action),
            ("", root.do_action),
            ("", root.exit_action),
        ]
        for fact in self._facts.values():
            scope = self._slot_scope(fact)
            slots += [
                (scope, fact.entry_action),
                (scope, fact.do_action),
                (scope, fact.exit_action),
            ]
        slots += [(_scope_of(t.source), t.effect) for t in self._transitions]
        for scope, slot in slots:
            for action in actions.inline_actions(slot):
                if not isinstance(action, syside.SendActionUsage):
                    continue
                event_name, _pairs = payload_signature(action)
                if self._needs.catalog is not None:
                    definition = constructed_payload_definition(action)
                    assert definition is not None
                    event_name = self._needs.catalog.channel_for(definition)
                self._sent_by_scope.setdefault(scope, {}).setdefault(
                    event_name, None
                )
                if send_via_port(action) is None:
                    self._self_sched_by_scope.setdefault(scope, set()).add(
                        event_name
                    )
                # Rosetta transports payloads as class instances over LF
                # ports, so every constructed type needs a generated
                # class, including attribute-less signal markers.
                definition = constructed_payload_definition(action)
                if definition is not None:
                    self._register_dataclass(definition)
        for scope, sent in self._sent_by_scope.items():
            for sig in sent:
                for other, handled in self._handled.items():
                    if other != scope and sig in handled:
                        raise UnsupportedConstructError(
                            f"signal {sig!r} is sent and accepted in "
                            "different composite scopes; cross-scope events "
                            "are not supported by rosetta."
                        )
        # Peer-accepted sends become output ports: record which scopes must
        # export each (the sending scope and every ancestor up to the root).
        for scope, sent in self._sent_by_scope.items():
            for sig in sent:
                if sig not in self._peer_accepts:
                    continue
                for enclosing in _enclosing(scope):
                    self._exported.setdefault(enclosing, {}).setdefault(
                        sig, None
                    )
        self._port_sigs = frozenset(self._exported.get("", {}))
        # Intentional alias; see __init__ docstring for the field.
        self._omitted_inputs = self._port_sigs

    # -- attributes -> parameters and state variables --

    def _attribute_split(self) -> tuple[list[Parameter], list[StateVar]]:
        """Split bindings into LF parameters (`in`) and state variables.

        ``inout`` (and undirected) attributes become state variables: a
        state var both reads and writes, which is the closest LF element.
        ``out`` promises an output port rosetta does not generate yet, so
        it is rejected rather than silently demoted.
        """
        parameters: list[Parameter] = []
        state_vars: list[StateVar] = []
        for binding in self._attributes:
            rendered = self._render_value(binding.value)
            if binding.direction is AttributeDirection.IN:
                if rendered is None:
                    raise UnsupportedConstructError(
                        f"`in` attribute {binding.name!r} has no default "
                        "value; LF reactor parameters require one."
                    )
                parameters.append(Parameter(binding.name, rendered))
            elif binding.direction is AttributeDirection.OUT:
                raise UnsupportedConstructError(
                    f"`out` attribute {binding.name!r} cannot be mapped; "
                    "rosetta does not yet generate output ports for "
                    "directed attributes. Drop the direction keyword."
                )
            elif rendered is not None:
                state_vars.append(StateVar(binding.name, rendered))
        return parameters, state_vars

    def _register_dataclass(self, definition: syside.Definition) -> None:
        """Register ``definition`` on the shared dataclass registry.

        Initial values render with the init codegen (``self_prefix=False``):
        a field default executes at import time, outside any reaction.
        """
        register_dataclass(
            definition,
            self._needs.dataclasses,
            self._init_codegen.render_expression,
        )

    def _render_value(self, value: AttributeValue) -> str | None:
        # Initial values render with the init codegen (self_prefix=False):
        # they execute outside a reaction, where `self.<attr>` does not
        # exist.
        return self._init_codegen.render_value(value, self._register_dataclass)

    # -- scope assembly --

    def _reactor_name(self, scope: str) -> str:
        return f"{self._name}_{scope.replace('::', '_')}"

    def _make_codegen(
        self,
        attribute_names: frozenset[str],
        local_names: frozenset[str] = frozenset(),
        payload_bindings: dict[syside.Feature, str] | None = None,
    ) -> LfPythonCodeGen:
        """Build a peer-aware code generator sharing the preamble registry."""
        return LfPythonCodeGen(
            attribute_names,
            needs=self._needs,
            port_signals=self._port_sigs,
            local_names=local_names,
            payload_bindings=payload_bindings,
        )

    def _scope_codegen(self, scope: str) -> LfPythonCodeGen:
        """The expression codegen for a scope's reaction bodies.

        Child scopes see NO attribute names: substate guards/actions
        referencing root-scope attributes fail loudly in codegen (the
        documented slice-6 scope limit).
        """
        if scope == "":
            return self._codegen
        return self._make_codegen(frozenset())

    def _scope_attribute_names(self, scope: str) -> frozenset[str]:
        return self._attribute_names if scope == "" else frozenset()

    def _composite_reactor(
        self, scope: str, container: StateFact, *, is_root: bool
    ) -> Reactor:
        """Assemble the reactor class for one composite scope.

        Child scopes' reactors are assembled first (recursively) and
        appended to ``self._reactors``, so the program lists children
        before their parents.
        """
        kids = self._children.get(scope, [])
        sent = list(self._sent_by_scope.get(scope, {}))
        inputs = [
            sig
            for sig in self._accepted.get(scope, {})
            if sig not in self._omitted_inputs
        ]
        gen = self._scope_codegen(scope)
        self._check_names(scope, kids, inputs, sent)
        inputs += [
            f"{sig}_origin"
            for sig in self._accepted.get(scope, {})
            if (scope, sig) in self._origin
        ]
        extra_state: list[StateVar] = []
        exits: list[tuple[str, list[str], tuple[str, ...]]] = []
        tail = _Trailing()
        modes = [
            self._child_mode(
                kid,
                scope,
                container,
                sent,
                gen,
                extra_state,
                exits,
                tail,
            )
            for kid in kids
        ]
        handler: list[Reaction] = []
        if scope in self._needs_done:
            modes.append(self._done_mode(scope, sent))
        self._check_state_names(scope, kids, sent, extra_state)
        if not is_root:
            inputs.append(ENTRY_ORDER_PORT)
        if self._reports(scope):
            inputs.append(EXIT_REQUEST_PORT)
            extra_state += [
                StateVar(ACTIVE_VAR, "None", reset=True),
                StateVar(EXIT_PENDING_VAR, "False", reset=True),
            ]
            handler.append(exit_handler(exits, sent, self._scope_ports(scope)))
        ported = tuple(self._exported.get(scope, {}))
        consumed = tuple(
            f"{sig}_consumed" for sig in self._consumed.get(scope, {})
        )
        parameters: list[Parameter]
        state_vars: list[StateVar]
        reactions: list[Reaction]
        if is_root:
            parameters, state_vars = self._attribute_split()
            outputs: tuple[str, ...] = (OUTPUT_PORT, *ported, *consumed)
            reactions = self._root_reactions(
                container, sent, self._scope_ports("")
            )
            name = self._name
        else:
            parameters = []
            state_vars = []
            exit_ports = tuple(
                name
                for target, port in self._exit_ports.get(scope, {}).items()
                for name in (port, self._late_port(scope, target))
                if name is not None
            )
            clashes = set(exit_ports) & {
                *inputs,
                *ported,
                *consumed,
                *sent,
                *(_simple(kid.name) for kid in kids),
            }
            if clashes:
                raise UnsupportedConstructError(
                    f"signal or state names {sorted(clashes)!r} collide "
                    "with Rosetta generated exit ports; rename them."
                )
            lifecycle = (
                (BUSY_PORT, SETTLED_PORT, EXIT_ACK_PORT)
                if self._reports(scope)
                else ()
            )
            outputs = (
                COMPLETED_PORT,
                OUTPUT_PORT,
                *exit_ports,
                *ported,
                *consumed,
                *lifecycle,
            )
            reactions = []
            name = self._reactor_name(scope)
        sched = self._self_sched_by_scope.get(scope, set())
        sent_self = [sig for sig in sent if sig in sched]
        action_list = [LogicalAction(f"{sig}_act") for sig in sent_self]
        if is_root and self._has_when:
            action_list.append(LogicalAction(CHANGE_ACT))
        return Reactor(
            name=name,
            parameters=tuple(parameters),
            inputs=tuple(inputs),
            outputs=outputs,
            state_vars=tuple(state_vars) + tuple(extra_state),
            actions=tuple(action_list),
            reactions=tuple(reactions),
            modes=tuple(modes),
            trailing_reactions=(
                *handler,
                *tail.chains,
                *tail.late,
                *tail.forwarding,
            ),
        )

    def _parallel_root_reactor(self, root: StateFact) -> Reactor:
        """Assemble a parallel ROOT machine: regions wired at reactor scope.

        The machine has no modes; completion is a join over all regions'
        ``completed`` ports that runs each completing region's exit, then,
        once all regions are finalized, the root's exit and requests stop.
        """
        regions = self._regions(root)
        sent = list(self._sent_by_scope.get("", {}))
        inputs = [
            sig
            for sig in self._accepted.get("", {})
            if sig not in self._omitted_inputs
        ]
        self._check_names("", regions, inputs, sent)
        instantiations: list[Instantiation] = []
        connections: list[Connection] = []
        reactions: list[Reaction] = list(
            self._root_reactions(root, sent, self._scope_ports(""), regions)
        )
        flags: list[str] = []
        root_gate_state: list[StateVar] = []
        root_gates: list[Reaction] = []
        for region in regions:
            r_simple = _simple(region.name)
            inst = f"c_{r_simple}"
            child = self._composite_reactor(region.name, region, is_root=False)
            self._reactors.append(child)
            if self._exit_ports.get(region.name):
                raise UnsupportedConstructError(
                    f"a transition escapes parallel region {region.name!r}; "
                    "a parallel root machine has no enclosing state to "
                    "receive it."
                )
            instantiations.append(Instantiation(inst, child.name))
            feed, gates, gate_state = self._region_feed("", region, inst)
            connections += feed
            root_gate_state += gate_state
            root_gates += gates
            reactions.append(self._reemit(inst, r_simple, prefix=""))
            reactions += [
                self._port_reemit(inst, sig)
                for sig in self._exported.get(region.name, {})
            ]
            flags.append(f"{r_simple}_done")
        # Gates precede consumption readers to avoid LF causality cycles.
        reactions[:0] = root_gates
        join_body: list[str] = []
        completing_regions = [
            region for region in regions if region.name in self._needs_done
        ]
        # Validate exits even when their regions have no completion path.
        for region, flag in zip(regions, flags, strict=True):
            region_exit = self._statements(region.exit_action, self._codegen)
            if region.name not in self._needs_done:
                continue
            inst = f"c_{_simple(region.name)}"
            join_body.append(f"if {inst}.{COMPLETED_PORT}.is_present:")
            join_body += [
                f"{_PY_INDENT}{line}"
                for line in (*region_exit, f"self.{flag} = True")
            ]
        condition = " and ".join(f"self.{flag}" for flag in flags)
        join_body.append(f"if {condition}:")
        join_body += [
            f"{_PY_INDENT}{line}"
            for line in (*self._root_exit, "request_stop()")
        ]
        # Unwritten LF ports cannot be read by Python reactions. Keep the
        # missing region's flag false so a partial completion cannot join.
        if completing_regions:
            reactions.append(
                make_reaction(
                    tuple(
                        f"c_{_simple(r.name)}.{COMPLETED_PORT}"
                        for r in completing_regions
                    ),
                    (),
                    tuple(join_body),
                    sent,
                    self._scope_ports(""),
                )
            )
        parameters, state_vars = self._attribute_split()
        state_vars += [StateVar(flag, "False", reset=True) for flag in flags]
        state_vars += root_gate_state
        self._check_state_names("", regions, sent, root_gate_state)
        ported = tuple(self._exported.get("", {}))
        sched = self._self_sched_by_scope.get("", set())
        sent_self = [sig for sig in sent if sig in sched]
        return Reactor(
            name=self._name,
            parameters=tuple(parameters),
            inputs=tuple(inputs),
            outputs=(OUTPUT_PORT, *ported),
            state_vars=tuple(state_vars),
            actions=tuple(LogicalAction(f"{sig}_act") for sig in sent_self),
            instantiations=tuple(instantiations),
            connections=tuple(connections),
            reactions=tuple(reactions),
        )

    def _is_reentered(self, fact: StateFact) -> bool:
        """Whether a transition can re-enter ``fact`` while it is active."""
        return any(
            state == fact.name or _is_ancestor(state, fact.name)
            for state in self._reentered
        )

    def _reports(self, scope: str) -> bool:
        """Whether a scope's reactor reports busy/settled to its parent.

        Subtrees below a tracked state report settlement for retained
        triggers. Subtrees of an escaped parallel state also expose the
        same exit request/acknowledgment protocol for sibling cleanup.
        """
        return scope != "" and any(
            state == scope or _is_ancestor(state, scope)
            for state in self._tracked | self._escaping.keys()
        )

    def _regions(self, parallel: StateFact) -> list[StateFact]:
        """Validate and return a parallel state's regions."""
        key = "" if parallel.parent is None else parallel.name
        regions = self._children.get(key, [])
        if not regions:
            raise UnsupportedConstructError(
                f"parallel state {parallel.name!r} declares no regions."
            )
        for region in regions:
            if region.kind is not StateKind.COMPOSITE:
                raise UnsupportedConstructError(
                    f"parallel region {region.name!r} must be a composite "
                    "state with its own entry; leaf or parallel regions are "
                    "not supported by rosetta."
                )
            for t in self._transitions:
                if t.source == region.name:
                    raise UnsupportedConstructError(
                        f"a transition is sourced at parallel region "
                        f"{region.name!r}; author interrupts on the parallel "
                        "state itself."
                    )
        return regions

    def _multi_trigger_states(self, scope: str) -> set[str]:
        """Simple names of states with >=2 distinct trigger groups in scope."""
        signals: dict[str, set[str | None]] = {}
        afters: dict[str, int] = {}
        for t in self._scope_transitions.get(scope, []):
            trigger = t.trigger
            if trigger is None:
                continue
            if isinstance(trigger, SignalTrigger):
                signals.setdefault(t.source, set()).update(
                    self._channels(trigger)
                )
            elif isinstance(trigger, (AfterTrigger, AtTrigger, WhenTrigger)):
                afters[t.source] = afters.get(t.source, 0) + 1
        out: set[str] = set()
        for source in set(signals) | set(afters):
            if len(signals.get(source, set())) + afters.get(source, 0) >= 2:
                out.add(_simple(source))
        return out

    def _check_names(
        self,
        scope: str,
        kids: list[StateFact],
        inputs: list[str],
        sent: list[str],
    ) -> None:
        channels = set(inputs) | set(self._exported.get(scope, {})) | set(sent)
        lifecycle = {
            name
            for kid in kids
            if kid.kind is not StateKind.LEAF and self._reports(kid.name)
            for name in (
                f"_{_simple(kid.name)}_busy",
                f"_{_simple(kid.name)}_pending",
                f"_{_simple(kid.name)}_sel",
            )
        }
        if self._reports(scope):
            lifecycle |= {
                BUSY_PORT,
                SETTLED_PORT,
                EXIT_REQUEST_PORT,
                EXIT_ACK_PORT,
                ACTIVE_VAR,
                EXIT_PENDING_VAR,
            }
        parallels = [kid.name for kid in kids if kid.kind is StateKind.PARALLEL]
        root = self._root
        if scope == "" and root is not None and root.kind is StateKind.PARALLEL:
            parallels.append("")
        lifecycle |= {
            f"{sig}_origin"
            for sig in self._accepted.get(scope, {})
            if (scope, sig) in self._origin
        }
        lifecycle |= {
            f"_{_simple(key) or 'root'}_{_simple(region)}_{sig}_wait"
            for key in parallels
            for sig, names in self._shared.get(key, {}).items()
            for region in names[1:]
        }
        generated = {
            *lifecycle,
            DONE_MODE,
            OUTPUT_PORT,
            COMPLETED_PORT,
            ENTRY_ORDER_PORT,
            "_sysmlc_types",
            *(f"{sig}_act" for sig in sent),
            *(f"{sig}_consumed" for sig in self._consumed.get(scope, {})),
            *(f"{name}_fired" for name in self._multi_trigger_states(scope)),
            *((CHANGE_ACT,) if self._has_when else ()),
        }
        conflicts = channels & generated
        if conflicts:
            raise UnsupportedConstructError(
                f"signal channels {sorted(conflicts)!r} collide with "
                "Rosetta generated identifiers"
            )
        reserved = {
            *lifecycle,
            DONE_MODE,
            OUTPUT_PORT,
            COMPLETED_PORT,
            ENTRY_ORDER_PORT,
            *inputs,
            *self._exported.get(scope, {}),
            *(f"{sig}_consumed" for sig in self._consumed.get(scope, {})),
            *(f"{sig}_act" for sig in sent),
            *(f"c_{_simple(kid.name)}" for kid in kids),
            *(f"{name}_fired" for name in self._multi_trigger_states(scope)),
            *((CHANGE_ACT,) if self._has_when else ()),
        }
        for fact in kids:
            if _simple(fact.name) in reserved:
                raise UnsupportedConstructError(
                    f"state name {_simple(fact.name)!r} collides with a name "
                    "rosetta generates (done, current_state, completed, "
                    "_entry_order, a signal port/action, or a <state>_fired "
                    "single-fire flag); rename the state."
                )

    def _check_state_names(
        self,
        scope: str,
        kids: list[StateFact],
        sent: list[str],
        generated: list[StateVar],
    ) -> None:
        """Reject model names that reuse a generated state variable.

        Checks the state variables this scope's reactor actually emits
        against its states, its signals and, at the root, the attributes
        sharing the reactor's state namespace.

        Raises:
            UnsupportedConstructError: A model name equals a generated
                state variable.
        """
        model = {
            *(_simple(kid.name) for kid in kids),
            *self._accepted.get(scope, {}),
            *self._exported.get(scope, {}),
            *sent,
            *(self._attribute_names if scope == "" else ()),
        }
        clashes = model & {var.name for var in generated}
        if clashes:
            raise UnsupportedConstructError(
                f"names {sorted(clashes)!r} collide with Rosetta generated "
                "state variables; rename them."
            )

    # -- mode assembly --

    def _observe_log(self, verb: str, simple: str) -> str:
        """An entry/exit DEBUG log line; flags the preamble for ``logging``."""
        self._needs.uses_logging = True
        return f'logging.debug("{verb} {self._name}.{simple}")'

    def _child_mode(
        self,
        fact: StateFact,
        scope: str,
        container: StateFact,
        sent: list[str],
        gen: LfPythonCodeGen,
        extra_state: list[StateVar],
        exits: list[tuple[str, list[str], tuple[str, ...]]],
        tail: _Trailing,
    ) -> Mode:
        """Translate one state of ``scope`` into a mode of its reactor.

        In a reporting scope the mode also contributes its forced-exit
        lines to ``exits`` (the scope's exit-request handler); a
        composite/parallel state below a tracked state adds to ``tail``
        the reactions that drain its children on departure, the late
        resolutions of deferred exit ports and its cleanup-dependent
        output forwarding.
        """
        simple = _simple(fact.name)
        outgoing = [
            t
            for t in self._scope_transitions.get(scope, [])
            if t.source == fact.name
        ]
        has_order = all(t.declaration_order is not None for t in outgoing)
        if has_order:
            outgoing.sort(key=lambda t: t.declaration_order or 0)
        for transition in outgoing:
            if transitions.self_loop_is_unstable(transition):
                raise UnsupportedConstructError(
                    "a self-loop transition would never stabilize (no "
                    "event, timer, or effect to break the loop)."
                )
        eventless: list[TransitionFact] = []
        signal_groups: dict[str, list[TransitionFact]] = {}
        signal_runs: list[tuple[str, list[TransitionFact]]] = []
        previous: str | None = None
        afters: list[TransitionFact] = []
        ats: list[TransitionFact] = []
        whens: list[TransitionFact] = []
        for transition in outgoing:
            trigger = transition.trigger
            if not isinstance(trigger, SignalTrigger):
                previous = None
            if trigger is None:
                eventless.append(transition)
            elif isinstance(trigger, SignalTrigger):
                for channel in self._channels(trigger):
                    signal_groups.setdefault(channel, []).append(transition)
                    if channel == previous:
                        signal_runs[-1][1].append(transition)
                    else:
                        signal_runs.append((channel, [transition]))
                    previous = channel
            elif isinstance(trigger, AfterTrigger):
                afters.append(transition)
            elif isinstance(trigger, AtTrigger):
                ats.append(transition)
            else:
                whens.append(transition)
        if not has_order:
            # Preserve legacy grouping without complete declaration ordinals.
            signal_runs = list(signal_groups.items())
        if all(t.declaration_order is not None for t in eventless):
            eventless.sort(key=lambda t: t.declaration_order or 0)
        lifecycle = fact.kind is not StateKind.LEAF and self._reports(fact.name)
        pending = f"_{simple}_pending" if lifecycle else None
        insts: tuple[str, ...] = ()
        if lifecycle:
            children = (
                [fact]
                if fact.kind is StateKind.COMPOSITE
                else self._regions(fact)
            )
            insts = tuple(f"c_{_simple(child.name)}" for child in children)
        busy_sources = tuple(f"{inst}.{BUSY_PORT}" for inst in insts)
        sel = f"_{simple}_sel" if lifecycle else None
        drain = (
            None
            if fact.name not in self._tracked
            else _Drain(
                f"_{simple}_sel",
                f"{insts[0]}.{EXIT_REQUEST_PORT}",
                {id(t): i for i, t in enumerate(outgoing)},
            )
        )
        occurrence = "_tag" if lifecycle else _ARRIVAL
        overtaking: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
        retained: list[tuple[int, list[str], tuple[str, ...]]] = []
        consumed_sources: list[tuple[int, tuple[str, ...]]] = []
        held: list[tuple[int, str]] = []

        # Observation exit log: prepended to BOTH exit-statement threads (the
        # default one below and the payload-aware ``group_exit`` for signal
        # transitions). It lands in every outgoing transition's reaction, but
        # only the firing transition runs, so each departure logs exactly once.
        exit_log = (
            [self._observe_log("exited", simple)] if self._observe else []
        )
        exit_stmts = [*exit_log, *self._statements(fact.exit_action, gen)]
        timers: list[Timer] = []
        mode_actions: list[LogicalAction] = []
        reactions: list[Reaction] = []
        entry_body = [f'{OUTPUT_PORT}.set("{simple}")']
        entry_effects = [OUTPUT_PORT]
        if self._observe:
            entry_body.insert(0, self._observe_log("entered", simple))
        if self._reports(scope):
            entry_body.append(f'self.{ACTIVE_VAR} = "{simple}"')
            if fact.kind is StateKind.LEAF:
                forced = [
                    *exit_stmts,
                    *self._exit_ack(scope),
                ]
                exits.append((simple, forced, (EXIT_ACK_PORT,)))
            else:
                request = f"{insts[0]}.{EXIT_REQUEST_PORT}"
                exits.append(
                    (
                        simple,
                        [
                            f"self.{sel} = ()",
                            f'self.{ACTIVE_VAR} = ""',
                            f"{request}.set(True)",
                        ],
                        (request,),
                    )
                )
        if self._reports(scope):
            entry_body.append(f"self.{EXIT_PENDING_VAR} = False")
        entry_body += self._statements(fact.entry_action, gen)
        if fact.do_action is not None:
            actions.require_inline_one_shot(fact.do_action)
            entry_body += self._statements(fact.do_action, gen)

        multi = (
            len(signal_runs)
            + len(afters)
            + len(ats)
            + len(whens)
            + bool(eventless and fact.kind is StateKind.LEAF)
            >= 2
        )
        fired_flag = f"{simple}_fired" if multi else None
        if fired_flag is not None:
            # Child-scope reactors are instantiated inside a parent `reset`
            # mode; lfc 0.11 requires their state vars be `reset state` (the
            # join-flag rule). Root-scope ("") flags stay plain `state`.
            extra_state.append(StateVar(fired_flag, "False", reset=scope != ""))
        pos = {id(t): i for i, t in enumerate(outgoing)}
        ordered: list[tuple[int, Reaction]] = []

        # Timer/action names are state-qualified: lfc flattens mode-local
        # declarations into one per-reactor C struct, so identical names in
        # two modes collide ("duplicate member" compile errors).
        for index, transition in enumerate(afters):
            assert isinstance(transition.trigger, AfterTrigger)
            after = transition.trigger.duration
            body, targets = self._dispatch(
                [transition],
                exit_stmts,
                scope,
                gen,
                fired=fired_flag,
                departing=pending,
                occurrence=occurrence,
                drain=drain,
            )
            if pending is not None:
                key = pos[id(transition)]
                retained.append((key, body, targets))
                body = self._retain(
                    body, key, "None", fact, insts, "an after trigger"
                )
            if isinstance(after, float):
                trigger_name = (
                    f"t_{simple}" if len(afters) == 1 else f"t_{simple}_{index}"
                )
                timers.append(Timer(trigger_name, render_duration(after)))
                delay_ns = str(round(after * 1e9))
            else:
                assert after is not None  # AFTER always carries a duration
                trigger_name = (
                    f"after_{simple}_act"
                    if len(afters) == 1
                    else f"after_{simple}_{index}_act"
                )
                mode_actions.append(LogicalAction(trigger_name))
                delay = gen.render_expression(after)
                entry_body.append(f"_after_ns = int(({delay}) * 1e9)")
                entry_body.append(f"{trigger_name}.schedule(_after_ns)")
                delay_ns = "_after_ns"
                entry_effects.append(trigger_name)
            if self._is_reentered(fact):
                due = f"_{trigger_name}_due"
                extra_state.append(StateVar(due, "None", reset=scope != ""))
                entry_body.append(
                    f"self.{due} = lf.time.logical_elapsed() + {delay_ns}"
                )
                body = when_due(due, body)
            if fired_flag is not None:
                body = [
                    f"if not self.{fired_flag}:",
                    *(f"{_PY_INDENT}{line}" for line in body),
                ]
            ordered.append(
                (
                    pos[id(transition)],
                    make_reaction(
                        (trigger_name,),
                        targets,
                        tuple(body),
                        sent,
                        self._scope_ports(scope),
                        sources=busy_sources,
                    ),
                )
            )

        for index, transition in enumerate(ats):
            assert isinstance(transition.trigger, AtTrigger)
            instant = transition.trigger.instant
            action_name = (
                f"at_{simple}_act"
                if len(ats) == 1
                else f"at_{simple}_{index}_act"
            )
            mode_actions.append(LogicalAction(action_name))
            if isinstance(instant, float):
                instant_ns = str(round(instant * 1e9))
            else:
                instant_ns = f"int(({gen.render_expression(instant)}) * 1e9)"
            entry_body.append(
                f"_at_delta = {instant_ns} - lf.time.logical_elapsed()"
            )
            entry_body.append("if _at_delta >= 0:")
            entry_body.append(f"{_PY_INDENT}{action_name}.schedule(_at_delta)")
            entry_effects.append(action_name)
            due = f"_{action_name}_due" if self._is_reentered(fact) else None
            if due is not None:
                extra_state.append(StateVar(due, "None", reset=scope != ""))
                entry_body.append(
                    f"self.{due} = lf.time.logical_elapsed() + _at_delta "
                    "if _at_delta >= 0 else None"
                )
            body, targets = self._dispatch(
                [transition],
                exit_stmts,
                scope,
                gen,
                fired=fired_flag,
                departing=pending,
                occurrence=occurrence,
                drain=drain,
            )
            if pending is not None:
                key = pos[id(transition)]
                retained.append((key, body, targets))
                body = self._retain(
                    body, key, "None", fact, insts, "an at trigger"
                )
            if due is not None:
                body = when_due(due, body)
            if fired_flag is not None:
                body = [
                    f"if not self.{fired_flag}:",
                    *(f"{_PY_INDENT}{line}" for line in body),
                ]
            ordered.append(
                (
                    pos[id(transition)],
                    make_reaction(
                        (action_name,),
                        targets,
                        tuple(body),
                        sent,
                        self._scope_ports(scope),
                        sources=busy_sources,
                    ),
                )
            )

        if whens:
            entry_body.append(f"{CHANGE_ACT}.schedule(0)")
            entry_effects.append(CHANGE_ACT)
        for index, transition in enumerate(whens):
            assert isinstance(transition.trigger, WhenTrigger)
            armed = (
                f"{simple}_w_armed"
                if len(whens) == 1
                else f"{simple}_w{index}_armed"
            )
            extra_state.append(StateVar(armed, "False", reset=scope != ""))
            entry_body.append(f"self.{armed} = False")
            cond = gen.render_expression(transition.trigger.condition)
            inner, targets = self._dispatch(
                [transition],
                exit_stmts,
                scope,
                gen,
                fired=fired_flag,
                departing=pending,
                occurrence=occurrence,
                drain=drain,
            )
            if pending is not None:
                key = pos[id(transition)]
                retained.append((key, inner, targets))
                inner = self._retain(
                    inner, key, "None", fact, insts, "a change trigger"
                )
            body = [
                f"if not self.{armed}:",
                f"{_PY_INDENT}if ({cond}):",
                f"{_PY_INDENT}{_PY_INDENT}self.{armed} = True",
                *(f"{_PY_INDENT}{_PY_INDENT}{line}" for line in inner),
            ]
            if fired_flag is not None:
                body = [
                    f"if not self.{fired_flag}:",
                    *(f"{_PY_INDENT}{line}" for line in body),
                ]
            ordered.append(
                (
                    pos[id(transition)],
                    make_reaction(
                        (CHANGE_ACT,),
                        targets,
                        tuple(body),
                        sent,
                        self._scope_ports(scope),
                        sources=busy_sources,
                    ),
                )
            )

        emitted_channels: set[str] = set()
        for signal, run in signal_runs:
            group = signal_groups[signal]
            if all(t.declaration_order is not None for t in group):
                group.sort(key=lambda t: t.declaration_order or 0)
            first_run = signal not in emitted_channels
            emitted_channels.add(signal)
            triggers: tuple[str, ...]
            is_input = signal not in self._omitted_inputs
            is_self_sched = signal in self._self_sched_by_scope.get(
                scope, set()
            )
            trig: list[str] = []
            if is_input:
                trig.append(signal)
            if is_self_sched:
                trig.append(f"{signal}_act")
            if not trig:
                # Peer-sent (an output, omitted from inputs) with no
                # self-directed send: nothing can deliver this signal to the
                # machine, so the local accept is unreachable. Drop it.
                logger.warning(
                    "accept of %r in %r is unreachable: the signal is only "
                    "sent out (via a port) and has no self-directed send to "
                    "deliver it locally; the transition is dropped",
                    signal,
                    self._name,
                )
                continue
            triggers = tuple(trig)
            prelude: list[str] = []
            group_gen = gen
            if is_input and is_self_sched:
                value = (
                    f"({signal}.value if {signal}.is_present "
                    f"else {signal}_act.value)"
                )
            elif is_input:
                value = f"{signal}.value"
            else:
                value = f"{signal}_act.value"
            payload = "None"
            features = [
                t.trigger.payload_feature
                for t in group
                if isinstance(t.trigger, SignalTrigger)
                and t.trigger.payload_feature is not None
            ]
            if pending is not None and features:
                prelude.append(f"_value = {value}")
                payload = value = "_value"
            bindings = dict.fromkeys(features, value)
            if bindings:
                group_gen = self._make_codegen(
                    self._scope_attribute_names(scope),
                    payload_bindings=bindings,
                )
            group_exit = [
                *exit_log,
                *self._statements(fact.exit_action, group_gen),
            ]
            consumed_flag = (
                f"{signal}_consumed"
                if signal in self._consumed.get(scope, {})
                else None
            )
            body, targets = self._dispatch(
                run,
                group_exit,
                scope,
                group_gen,
                consumed=consumed_flag,
                fired=fired_flag,
                departing=pending,
                occurrence=self._arrival(scope, signal),
                drain=drain,
                payload=payload,
            )
            key = min(pos[id(t)] for t in group)
            if pending is not None:
                if first_run:
                    retained_body, retained_targets = self._dispatch(
                        group,
                        group_exit,
                        scope,
                        group_gen,
                        consumed=consumed_flag,
                        fired=fired_flag,
                        departing=pending,
                        occurrence="_tag",
                        drain=drain,
                        payload=payload,
                    )
                    retained.append((key, retained_body, retained_targets))
                    if consumed_flag is not None:
                        held.append((key, consumed_flag))
                    body = self._retain(
                        body,
                        key,
                        payload,
                        fact,
                        insts,
                        f"signal {signal}",
                        consumed_flag,
                        self._arrival(scope, signal),
                    )
                else:
                    body = [
                        f"if not ({busy_condition(simple, insts)}):",
                        *(f"{_PY_INDENT}{line}" for line in body),
                    ]
                body = [*prelude, *body]
                prelude = []
            guard_srcs = self._interrupt_guard_sources(fact, signal)
            if guard_srcs and pending is not None and first_run:
                consumed_sources.append((key, guard_srcs))
            if guard_srcs:
                cond = " or ".join(
                    f"({src}.is_present and {src}.value not in (None, False))"
                    for src in guard_srcs
                )
                arrived = " or ".join(f"{t}.is_present" for t in triggers)
                body = [
                    f"if ({arrived}) and not ({cond}):",
                    *(f"{_PY_INDENT}{line}" for line in body),
                ]
                # {sig}_consumed ports are reaction TRIGGERS, not sources/uses,
                # so LF schedules this reaction whenever the flag port fires.
                triggers = (*triggers, *guard_srcs)
            if (scope, signal) in self._origin:
                since = f"_{simple}_since"
                if not any(var.name == since for var in extra_state):
                    extra_state.append(
                        StateVar(since, "None", reset=scope != "")
                    )
                    entry_body.insert(1, f"self.{since} = {_ARRIVAL}")
                body = [
                    f"if {self._arrival(scope, signal)} >= self.{since}:",
                    *(f"{_PY_INDENT}{line}" for line in body),
                ]
            if fired_flag is not None:
                body = [
                    f"if not self.{fired_flag}:",
                    *(f"{_PY_INDENT}{line}" for line in body),
                ]
            ordered.append(
                (
                    pos[id(run[0])] if has_order else key,
                    make_reaction(
                        triggers,
                        targets,
                        tuple(prelude + body),
                        sent,
                        self._scope_ports(scope),
                        sources=(
                            *busy_sources,
                            *(
                                (f"{signal}_origin",)
                                if (scope, signal) in self._origin
                                else ()
                            ),
                        ),
                    ),
                )
            )

        ordered.sort(key=lambda item: item[0])
        reactions.extend(reaction for _, reaction in ordered)
        if fired_flag is not None:
            entry_body.append(f"self.{fired_flag} = False")

        instantiations: list[Instantiation] = []
        connections: list[Connection] = []
        forwarding: list[Reaction] = [] if lifecycle else reactions
        resolutions: list[Reaction] = [] if lifecycle else reactions
        region_exits: list[list[str]] = []
        region_flags: list[str] = []
        if retained:
            overtaking = (tuple(self._overtaking(fact, insts)), busy_sources)

        if fact.kind is StateKind.LEAF:
            if eventless:
                # A guarded eventless self-loop with an effect passes the
                # stability check (quake parity) but re-runs this entry
                # reaction on every reset; it relies on the guard eventually
                # going false.
                body, targets = self._dispatch(
                    eventless,
                    exit_stmts,
                    scope,
                    gen,
                    fired=fired_flag,
                    occurrence=_CONTINUATION,
                )
                entry_body += body
                entry_effects += list(targets)
            if self._reports(scope):
                settle = f"{SETTLED_PORT}.set(True)"
                if not eventless:
                    entry_body.append(settle)
                elif all(t.guard is not None for t in eventless):
                    entry_body += ["else:", f"{_PY_INDENT}{settle}"]
                # LF's Python target needs a possible writer even for absent ports.
                entry_effects += [BUSY_PORT, SETTLED_PORT]
        elif fact.kind is StateKind.COMPOSITE:
            inst = f"c_{simple}"
            child = self._composite_reactor(fact.name, fact, is_root=False)
            self._reactors.append(child)
            instantiations.append(Instantiation(inst, child.name))
            entry_effects.append(f"{inst}.{ENTRY_ORDER_PORT}")
            connections += [
                Connection(sig, f"{inst}.{sig}")
                for sig in self._accepted.get(fact.name, {})
                if sig not in self._omitted_inputs
            ]
            connections += [
                Connection(f"{sig}_origin", f"{inst}.{sig}_origin")
                for sig in self._accepted.get(fact.name, {})
                if (fact.name, sig) in self._origin
            ]
            reactions.append(self._reemit(inst, simple, prefix=f"{simple}."))
            forwarding += [
                self._port_reemit(inst, sig)
                for sig in self._exported.get(fact.name, {})
            ]
            reactions += [
                self._consumed_reemit(inst, sig, pending, held)
                for sig in self._consumed.get(fact.name, {})
                if sig in self._consumed.get(scope, {})
            ]
            resolutions += self._exit_resolutions(
                inst,
                fact.name,
                exit_stmts,
                scope,
                sent,
                pending,
                overtaking,
                tail.late,
                held,
            )
            if eventless:
                # Completion semantics: an eventless transition out of a
                # composite fires when the child completes, never on entry.
                body, targets = self._dispatch(
                    eventless,
                    exit_stmts,
                    scope,
                    gen,
                    departing=pending,
                    occurrence=_CONTINUATION,
                )
                body, declined = declining(pending, held, body)
                reactions.append(
                    make_reaction(
                        (f"{inst}.{COMPLETED_PORT}",),
                        (*targets, *declined),
                        tuple(body),
                        sent,
                        self._scope_ports(scope),
                    )
                )
        else:  # StateKind.PARALLEL
            regions = self._regions(fact)
            region_gates: list[Reaction] = []
            for region in regions:
                r_simple = _simple(region.name)
                inst = f"c_{r_simple}"
                child = self._composite_reactor(
                    region.name, region, is_root=False
                )
                self._reactors.append(child)
                instantiations.append(Instantiation(inst, child.name))
                entry_effects.append(f"{inst}.{ENTRY_ORDER_PORT}")
                feed, gates, gate_state = self._region_feed(
                    fact.name, region, inst
                )
                connections += feed
                extra_state += gate_state
                region_gates += gates
                # LF does not reset root-scope gate state.
                entry_body += [f"self.{var.name} = None" for var in gate_state]
                reactions.append(
                    self._reemit(inst, r_simple, prefix=f"{simple}.{r_simple}.")
                )
                forwarding += [
                    self._port_reemit(inst, sig)
                    for sig in self._exported.get(region.name, {})
                ]
                reactions += [
                    self._consumed_reemit(inst, sig, pending, held)
                    for sig in self._consumed.get(region.name, {})
                    if sig in self._consumed.get(scope, {})
                ]
                resolutions += self._exit_resolutions(
                    inst,
                    region.name,
                    exit_stmts,
                    scope,
                    sent,
                    pending,
                    overtaking,
                    tail.late,
                    held,
                )
                # The region's own entry/do run on parallel-state entry; its
                # exit runs in the completion join when the region is ready.
                entry_body += self._statements(region.entry_action, gen)
                if region.do_action is not None:
                    actions.require_inline_one_shot(region.do_action)
                    entry_body += self._statements(region.do_action, gen)
                # Render even without a completion path, to validate it.
                region_exits.append(self._statements(region.exit_action, gen))
            # Gates precede consumption readers to avoid LF causality cycles.
            reactions[:0] = region_gates
            completing_regions = [
                region for region in regions if region.name in self._needs_done
            ]
            if completing_regions:
                flags = [f"{simple}_{_simple(r.name)}_done" for r in regions]
                region_flags = flags
                extra_state += [
                    StateVar(flag, "False", reset=True) for flag in flags
                ]
                entry_body += [f"self.{flag} = False" for flag in flags]
                # Finalize ready regions in declaration order.
                join_body: list[str] = []
                for region, flag, region_exit in zip(
                    regions, flags, region_exits, strict=True
                ):
                    if region.name not in self._needs_done:
                        continue
                    inst = f"c_{_simple(region.name)}"
                    join_body.append(f"if {inst}.{COMPLETED_PORT}.is_present:")
                    join_body += [
                        f"{_PY_INDENT}{line}"
                        for line in (*region_exit, f"self.{flag} = True")
                    ]
                targets: tuple[str, ...] = ()
                if eventless:
                    body, targets = self._dispatch(
                        eventless,
                        exit_stmts,
                        scope,
                        gen,
                        departing=pending,
                        occurrence=_CONTINUATION,
                    )
                    body, declined = declining(pending, held, body)
                    targets = (*targets, *declined)
                    condition = " and ".join(f"self.{flag}" for flag in flags)
                    join_body.append(f"if {condition}:")
                    join_body += [f"{_PY_INDENT}{line}" for line in body]
                reactions.append(
                    make_reaction(
                        tuple(
                            f"c_{_simple(r.name)}.{COMPLETED_PORT}"
                            for r in completing_regions
                        ),
                        targets,
                        tuple(join_body),
                        sent,
                        self._scope_ports(scope),
                    )
                )

        if pending is not None:
            busy = f"_{simple}_busy"
            extra_state += [
                StateVar(busy, "None", reset=scope != ""),
                StateVar(pending, "None", reset=scope != ""),
            ]
            if sel is not None:
                extra_state.append(StateVar(sel, "None", reset=scope != ""))
                entry_body.append(f"self.{sel} = None")
            entering = ", ".join(json.dumps(inst) for inst in insts)
            entry_body += [
                f"self.{busy} = {{{entering}}}",
                f"self.{pending} = ()",
            ]
            # Finalize regions and automatic departures before pending triggers.
            reactions.append(
                lifecycle_reaction(
                    simple,
                    insts,
                    retained,
                    consumed_sources,
                    sent,
                    held,
                    self._overtaking(fact, insts),
                    report_upward=self._reports(scope),
                    ports=self._scope_ports(scope),
                )
            )
            tail.chains += self._drain_chain(
                fact,
                scope,
                insts,
                exit_stmts,
                region_exits,
                region_flags,
                outgoing,
                sent,
            )
            tail.forwarding += forwarding
            reactions += resolutions

        entry = make_reaction(
            ("reset", "startup"),
            tuple(entry_effects),
            tuple(entry_body),
            sent,
            self._scope_ports(scope),
            sources=() if scope == "" else (ENTRY_ORDER_PORT,),
        )
        return Mode(
            name=simple,
            initial=(fact.name == container.initial_substate),
            timers=tuple(timers),
            actions=tuple(mode_actions),
            instantiations=tuple(instantiations),
            connections=tuple(connections),
            reactions=(entry, *reactions),
        )

    def _reemit(self, inst: str, region: str, *, prefix: str) -> Reaction:
        """Re-announce a child's ``current_state`` with a dotted path.

        For a composite child, ``prefix`` is ``"<mode>."``; for a parallel
        region, ``"<mode>.<region>."``; for a parallel root's region, the
        prefix is ``"<region>."`` built from the region name itself.
        """
        if not prefix:
            prefix = f"{region}."
        return Reaction(
            (f"{inst}.{OUTPUT_PORT}",),
            (OUTPUT_PORT,),
            (f'{OUTPUT_PORT}.set("{prefix}" + {inst}.{OUTPUT_PORT}.value)',),
        )

    def _port_reemit(self, inst: str, sig: str) -> Reaction:
        """Forward a child's ported signal out of this scope's reactor."""
        return Reaction(
            (f"{inst}.{sig}",), (sig,), (f"{sig}.set({inst}.{sig}.value)",)
        )

    def _consumed_reemit(
        self,
        inst: str,
        sig: str,
        pending: str | None,
        held: list[tuple[int, str]],
    ) -> Reaction:
        """Forward a child's consumption of ``sig`` out of this scope.

        While this state's own candidate still retains the same signal, a
        descendant's decline is forwarded as held (``None``): the occurrence
        stays undecided for the enclosing region until that candidate
        accepts or declines it.

        Args:
            inst: The child reactor instance.
            sig: The consumed signal.
            pending: The state's pending-trigger variable, or None.
            held: Retained signal keys and their consumption flags.
        """
        flag = f"{sig}_consumed"
        keys = [key for key, held_flag in held if held_flag == flag]
        if pending is None or not keys:
            return self._port_reemit(inst, flag)
        return Reaction(
            (f"{inst}.{flag}",),
            (flag,),
            (
                f"_value = {inst}.{flag}.value",
                f"if _value is False and self.{pending} and "
                f"self.{pending}[0] in ({''.join(f'{k}, ' for k in keys)}):",
                f"{_PY_INDENT}_value = None",
                f"{flag}.set(_value)",
            ),
        )

    def _exit_resolutions(
        self,
        inst: str,
        exited_scope: str,
        exit_stmts: list[str],
        scope: str,
        sent: list[str],
        departing: str | None = None,
        overtaking: tuple[tuple[str, ...], tuple[str, ...]] = ((), ()),
        trailing: list[Reaction] | None = None,
        held: list[tuple[int, str]] | None = None,
    ) -> list[Reaction]:
        """Resolve a child's escape and finish exited enclosing states.

        Early ports select the target mode or re-raise the selection.
        At a crossed parallel boundary, selection starts the existing
        child drain chain. Other enclosing states run their exits when
        the child's late cleanup output arrives, preserving the source
        exit and effect before enclosing exits.

        ``departing`` names the pending-trigger variable cleared when the
        source activation ends. ``overtaking`` contains checks against a
        later escape passing a retained occurrence, and their busy-port
        dependencies. ``held`` contains consumption flags to decline when
        their retained occurrence loses its source activation.

        Deferred cleanup reactions go in ``trailing`` after the drain
        chains. Keeping early selection separate from late completion
        avoids a static LF dependency cycle through the exit requests.
        """
        out: list[Reaction] = []
        closing = [f"self.{departing} = None"] if departing else []
        parent = self._facts.get(_scope_of(exited_scope))
        parallel = (
            parent
            if parent is not None and parent.kind is StateKind.PARALLEL
            else None
        )
        for target, port in self._exit_ports.get(exited_scope, {}).items():
            effect, set_line = self._resolve_target(scope, target)
            late = self._late_port(exited_scope, target)
            ending = [set_line, *closing]
            if parallel is not None:
                selected = f"_{_simple(parallel.name)}_sel"
                key = self._escaping[parallel.name][target]
                request = f"c_{_simple(self._regions(parallel)[0].name)}.{EXIT_REQUEST_PORT}"
                message = (
                    f"{self._source_qn}: competing departures of parallel "
                    f"state {self._display(parallel)} at one tag are not supported"
                )
                ending = [
                    f"if self.{selected} is not None:",
                    f"{_PY_INDENT}raise RuntimeError({json.dumps(message)})",
                    f"self.{selected} = ({-key - 1}, None)",
                    *ending,
                    f"{request}.set(True)",
                ]
                early_effects = (effect, request)
            else:
                early_effects = (effect,)
                if late is None:
                    ending = [*exit_stmts, *ending]
            if self._reports(scope):
                ending.append(f'self.{ACTIVE_VAR} = ""')
            ending, declined = declining(departing, held or [], ending)
            body = (*overtaking[0], *ending)
            if parallel is None and late is not None:
                assert trailing is not None
                outer = self._late_port(scope, target)
                final = [
                    *exit_stmts,
                    *(() if outer is None else (f"{outer}.set(True)",)),
                    *self._exit_ack(scope),
                ]
                trailing.append(
                    make_reaction(
                        (f"{inst}.{late}",),
                        (
                            *(() if outer is None else (outer,)),
                            *((EXIT_ACK_PORT,) if self._reports(scope) else ()),
                        ),
                        tuple(final) or ("pass",),
                        sent,
                        self._scope_ports(scope),
                    )
                )
            elif parallel is None and self._reports(scope):
                body = (*body, *self._exit_ack(scope))
                early_effects += (EXIT_ACK_PORT,)
            out.append(
                make_reaction(
                    (f"{inst}.{port}",),
                    (*early_effects, *declined),
                    body,
                    sent,
                    self._scope_ports(scope),
                    sources=overtaking[1],
                )
            )
        return out

    def _exit_ack(self, scope: str) -> list[str]:
        """Finish a scope's cleanup and acknowledge an outstanding request."""
        if not self._reports(scope):
            return []
        return [
            f"self.{ACTIVE_VAR} = None",
            f"if self.{EXIT_PENDING_VAR}:",
            f"{_PY_INDENT}{EXIT_ACK_PORT}.set(True)",
            f"{_PY_INDENT}self.{EXIT_PENDING_VAR} = False",
        ]

    def _drain_chain(
        self,
        fact: StateFact,
        scope: str,
        insts: tuple[str, ...],
        exit_stmts: list[str],
        region_exits: list[list[str]],
        region_flags: list[str],
        outgoing: list[TransitionFact],
        sent: list[str],
    ) -> list[Reaction]:
        """Drain a composite/parallel state's children, then finish it.

        Each child acknowledges its exit request once its active subtree
        has exited. Parallel regions drain one at a time in declaration
        order; after each acknowledgment the region's own exit runs unless
        the region already finalized naturally. Once the last child
        acknowledges, a departure the state selected itself runs the
        state's exit and the recorded transition's effect, with the
        recorded payload, then raises its exit port if it leaves the
        scope; otherwise the request came from an enclosing state, so the
        state's exit runs and the scope acknowledges upward. Its selection
        slot is None while idle, () for an ancestor request, or (key,
        payload) for a selected departure. Negative keys identify
        descendant-escape continuations.

        Args:
            fact: The source state.
            scope: The assembling scope (resolves targets and exit ports).
            insts: The source state's child reactor instances.
            exit_stmts: Rendered exit statements of the source state.
            region_exits: Per region, its rendered exit statements; empty
                for a composite state.
            region_flags: Per region, its natural-finalization flag, or
                empty when no region can finalize.
            outgoing: The source state's outgoing transitions.
            sent: Signals sent in this scope.
        """
        simple = _simple(fact.name)
        sel = f"self._{simple}_sel"
        final = [
            f"if {sel}:",
            f"{_PY_INDENT}_key, _value = {sel}",
        ]
        effects: list[str] = []
        keyword = "if"
        for target, key in self._escaping.get(fact.name, {}).items():
            late = self._late_port(scope, target)
            lines = [*exit_stmts]
            if late is not None:
                lines.append(f"{late}.set(True)")
                effects.append(late)
            final.append(f"{_PY_INDENT}{keyword} _key == {-key - 1}:")
            final += [f"{_PY_INDENT * 2}{line}" for line in lines or ["pass"]]
            keyword = "elif"
        for key, transition in enumerate(outgoing):
            if transition.trigger is None:
                continue
            features = (
                [transition.trigger.payload_feature]
                if isinstance(transition.trigger, SignalTrigger)
                and transition.trigger.payload_feature is not None
                else []
            )
            gen = self._make_codegen(
                self._scope_attribute_names(scope),
                payload_bindings=dict.fromkeys(features, "_value"),
            )
            exit_log = (
                [self._observe_log("exited", simple)] if self._observe else []
            )
            lines = [
                *exit_log,
                *self._statements(fact.exit_action, gen),
                *self._statements(transition.effect, gen),
            ]
            late = self._late_port(scope, transition.target)
            if late is not None:
                lines.append(f"{late}.set(True)")
                effects.append(late)
            final.append(f"{_PY_INDENT}{keyword} _key == {key}:")
            final += [f"{_PY_INDENT * 2}{line}" for line in lines or ["pass"]]
            keyword = "elif"
        final += [
            "else:",
            *(f"{_PY_INDENT}{line}" for line in exit_stmts or ["pass"]),
            f"{sel} = None",
            *self._exit_ack(scope),
        ]
        if self._reports(scope):
            effects.append(EXIT_ACK_PORT)
        chain: list[Reaction] = []
        for index, inst in enumerate(insts):
            body: list[str] = []
            if region_exits:
                region_exit = region_exits[index] or ["pass"]
                if region_flags:
                    body.append(f"if not self.{region_flags[index]}:")
                    body += [f"{_PY_INDENT}{line}" for line in region_exit]
                else:
                    body += region_exit
            if index + 1 < len(insts):
                request = f"{insts[index + 1]}.{EXIT_REQUEST_PORT}"
                body.append(f"{request}.set(True)")
                step_effects: tuple[str, ...] = (request,)
            else:
                body += final
                step_effects = tuple(effects)
            chain.append(
                make_reaction(
                    (f"{inst}.{EXIT_ACK_PORT}",),
                    step_effects,
                    (
                        f"if {sel} is not None:",
                        *(f"{_PY_INDENT}{line}" for line in body),
                    ),
                    sent,
                    self._scope_ports(scope),
                )
            )
        return chain

    def _retain(
        self,
        body: list[str],
        key: int,
        payload: str,
        fact: StateFact,
        insts: tuple[str, ...],
        what: str,
        consumed: str | None = None,
        arrival: str = _ARRIVAL,
    ) -> list[str]:
        """Resolve a source state's names and retained-overlap diagnostic."""
        message = (
            f"{self._source_qn}: {what} arrived at state "
            f"{self._display(fact)} while an earlier event is retained "
            "until its substates settle; overlapping retained events are "
            "not supported"
        )
        return retain(
            body,
            key,
            payload,
            _simple(fact.name),
            insts,
            message,
            consumed,
            arrival,
        )

    def _overtaking(self, fact: StateFact, insts: tuple[str, ...]) -> list[str]:
        """Resolve a source state's names and overtaking diagnostic."""
        message = (
            f"{self._source_qn}: a substate of {self._display(fact)} "
            "accepted an event that arrived after an event retained for "
            f"{self._display(fact)}; reordering overlapping events is not "
            "supported"
        )
        return overtaking(_simple(fact.name), insts, message)

    def _display(self, fact: StateFact) -> str:
        """A state's dotted path, as model-facing diagnostics name it."""
        return fact.name.replace("::", ".")

    def _resolving_scope(self, scope: str) -> str:
        """The scope whose reactor resolves exit ports raised by ``scope``.

        A composite's reactor sits in its parent scope's reactor; a
        parallel region's reactor sits in the reactor enclosing the
        parallel state.
        """
        parent = _scope_of(scope)
        if parent and self._facts[parent].kind is StateKind.PARALLEL:
            return _scope_of(parent)
        return parent

    def _late_port(
        self, scope: str, target: str | CompletionTarget
    ) -> str | None:
        """The late exit port for ``(scope, target)``, if it is deferred."""
        if isinstance(target, CompletionTarget):
            return None
        if (scope, target) not in self._deferred:
            return None
        return f"{self._exit_ports[scope][target]}_done"

    def _resolve_target(
        self, scope: str, target: str | CompletionTarget
    ) -> tuple[str, str]:
        """Render a transition target as (reaction effect, body line)."""
        if isinstance(target, CompletionTarget):
            self._needs_done.add(scope)
            return f"reset({DONE_MODE})", f"{DONE_MODE}.set()"
        if _scope_of(target) == scope:
            mode = _simple(target)
            return f"reset({mode})", f"{mode}.set()"
        # The target lives in an enclosing scope: raise a dedicated exit
        # port (one per distinct target) for the parent to resolve.
        ports = self._exit_ports.setdefault(scope, {})
        port = ports.setdefault(target, f"exit_{len(ports)}")
        return port, f"{port}.set(True)"

    def _interrupt_guard_sources(
        self, fact: StateFact, signal: str
    ) -> tuple[str, ...]:
        """Child-port refs a guarded group interrupt reads, or ``()``.

        For a conflict on ``(fact, signal)`` the boundary children are
        ``fact`` itself (a composite) or its consuming regions (parallel);
        each exposes a ``{signal}_consumed`` output.
        """
        boundaries = self._guarded_interrupts.get(fact.name, {}).get(signal)
        if not boundaries:
            return ()
        return tuple(
            f"c_{_simple(boundary)}.{signal}_consumed"
            for boundary in boundaries
        )

    def _scope_ports(self, scope: str) -> tuple[str, ...]:
        """Peer-accepted signals a scope sends (so its sends set ports)."""
        return tuple(
            sig
            for sig in self._sent_by_scope.get(scope, {})
            if sig in self._peer_accepts
        )

    def _dispatch(
        self,
        group: list[TransitionFact],
        exit_stmts: list[str],
        scope: str,
        gen: LfPythonCodeGen,
        consumed: str | None = None,
        fired: str | None = None,
        departing: str | None = None,
        occurrence: str = _ARRIVAL,
        drain: _Drain | None = None,
        payload: str = "None",
    ) -> tuple[list[str], tuple[str, ...]]:
        """Render a same-trigger group as a first-match if/elif dispatch.

        Declaration order is firing priority; a guardless branch always
        fires, so it closes the chain (as ``else`` when guards precede it).
        When ``consumed`` is set (a ``{sig}_consumed`` output), each firing
        branch raises it so an enclosing group interrupt on the same signal
        can yield to this inner transition (UML/SCXML inner-first). In a
        reporting scope each firing branch also reports busy with
        ``occurrence``, so the enclosing state waits until the transition
        settles and can tell which occurrence selected it; consumption
        carries the same value there.

        Args:
            group: The transitions sharing the same trigger.
            exit_stmts: Rendered exit statements of the state being left.
            scope: The assembling scope (resolves targets and exit ports).
            gen: The code generator for guard and statement rendering.
            consumed: The consumption-flag output name, or None.
            fired: A per-mode single-fire flag; each firing branch sets
                `self.<fired> = True`, or None.
            departing: The source state's pending-trigger variable; each
                firing branch clears it to ``None`` because the source
                activation ends, or None.
            occurrence: Python expression for the selecting occurrence's
                arrival tag, or ``()`` for an eventless continuation.
            drain: For a draining source state, each firing branch records
                its key and ``payload`` and requests the children's exits
                instead of running the exit, effect and any exit port; the
                drain continuation runs those. A mode switch is still
                declared here and applies after the tag's cleanup. None
                runs them inline.
            payload: Python expression for the payload a drained branch
                records.
        """
        body: list[str] = []
        effects: dict[str, None] = {}
        reports = self._reports(scope)
        for index, transition in enumerate(group):
            effect, set_line = self._resolve_target(scope, transition.target)
            late = self._late_port(scope, transition.target)
            if drain is None:
                effects.setdefault(effect, None)
                branch = (
                    exit_stmts
                    + self._statements(transition.effect, gen)
                    + [set_line]
                )
                if late is not None:
                    effects.setdefault(late, None)
                    branch.append(f"{late}.set(True)")
            else:
                key = drain.keys[id(transition)]
                effects.setdefault(drain.request, None)
                branch = [
                    f"self.{drain.sel} = ({key}, {payload})",
                    f"{drain.request}.set(True)",
                ]
                effects.setdefault(effect, None)
                branch.append(set_line)
                if transition.source in self._escaping:
                    fact = self._facts[transition.source]
                    message = (
                        f"{self._source_qn}: competing departures of parallel "
                        f"state {self._display(fact)} at one tag are not supported"
                    )
                    branch = [
                        f"if self.{drain.sel} is not None:",
                        f"{_PY_INDENT}raise RuntimeError({json.dumps(message)})",
                        *branch,
                    ]
            if reports:
                if drain is None:
                    branch += self._exit_ack(scope)
                    effects[EXIT_ACK_PORT] = None
                else:
                    branch.append(f'self.{ACTIVE_VAR} = ""')
            if consumed is not None:
                flag = occurrence if reports else "True"
                branch = [*branch, f"{consumed}.set({flag})"]
            if fired is not None:
                branch = [*branch, f"self.{fired} = True"]
            if departing is not None:
                branch = [*branch, f"self.{departing} = None"]
            if reports:
                branch = [*branch, report_busy(occurrence)]
            if transition.guard is None:
                if index == 0:
                    body += branch
                else:
                    body.append("else:")
                    body += [f"{_PY_INDENT}{line}" for line in branch]
                break  # later branches are unreachable
            keyword = "if" if index == 0 else "elif"
            guard = gen.render_expression(transition.guard)
            body.append(f"{keyword} {guard}:")
            body += [f"{_PY_INDENT}{line}" for line in branch]
        if consumed is not None:
            effects[consumed] = None
        if reports:
            effects[BUSY_PORT] = None
        return body, tuple(effects)

    def _done_mode(self, scope: str, sent: list[str]) -> Mode:
        """The final mode: the root exits and stops; a child completes."""
        is_root = scope == ""
        body = [f'{OUTPUT_PORT}.set("{DONE_MODE}")']
        effects = [OUTPUT_PORT]
        if is_root:
            body += [*self._root_exit, "request_stop()"]
        else:
            body.append(f"{COMPLETED_PORT}.set(True)")
            effects.append(COMPLETED_PORT)
        if self._reports(scope):
            body += [f"self.{ACTIVE_VAR} = None", f"{SETTLED_PORT}.set(True)"]
            effects += [BUSY_PORT, SETTLED_PORT]
        entry = make_reaction(
            ("reset", "startup"),
            tuple(effects),
            tuple(body),
            sent,
            self._scope_ports("") if is_root else (),
            sources=() if is_root else (ENTRY_ORDER_PORT,),
        )
        return Mode(name=DONE_MODE, reactions=(entry,))

    def _root_reactions(
        self,
        root: StateFact,
        sent: list[str],
        ports: tuple[str, ...] = (),
        regions: list[StateFact] | None = None,
    ) -> list[Reaction]:
        """Render the root/region startup entry and ``do`` actions."""
        gen = self._codegen
        body: list[str] = []
        for fact in (root, *(regions or ())):
            body += self._statements(fact.entry_action, gen)
            if fact.do_action is not None:
                actions.require_inline_one_shot(fact.do_action)
                body += self._statements(fact.do_action, gen)
        out: list[Reaction] = []
        if body:
            order = tuple(
                f"c_{_simple(region.name)}.{ENTRY_ORDER_PORT}"
                for region in regions or ()
            )
            out.append(
                make_reaction(("startup",), order, tuple(body), sent, ports)
            )
        return out

    def _statements(
        self,
        action: syside.ActionUsage | None,
        codegen: LfPythonCodeGen,
    ) -> list[str]:
        return [
            line
            for candidate in actions.inline_actions(action)
            for line in codegen.render_action(candidate).split("\n")
            if line
        ]


def finalize(
    program: LfProgram,
    needs: PreambleNeeds,
) -> LfProgram:
    """Attach the companion module + ``files:`` to a built program."""
    companion = needs.companion_module_lines()
    file_opt = files_option(
        needs.types_module if companion else None,
        needs.external,
    )
    options = program.target_options + ((file_opt,) if file_opt else ())
    for event_name, port in sorted(needs.undeliverable_sends):
        logger.warning(
            "machine %r: send %r via %r has no connected peer; the signal "
            "is not delivered (use `send ... to <own port>` for a "
            "self-event)",
            program.reactor.name,
            event_name,
            port,
        )
    aliases: dict[str, list[str]] = {}
    if needs.catalog is not None:
        for channel in needs.catalog.qualified_channels.values():
            definition = needs.catalog.definition_for(channel)
            if definition.name is not None:
                aliases.setdefault(definition.name, []).append(channel)
    return replace(
        program,
        target_options=options,
        types_module_name=needs.types_module if companion else None,
        types_module_lines=tuple(companion),
        signal_aliases=tuple(
            (name, channels[0])
            for name, channels in sorted(aliases.items())
            if len(channels) == 1
        ),
        signal_channels=tuple(
            needs.catalog.qualified_channels.items() if needs.catalog else ()
        ),
    )


def build_program(
    model: syside.Model,
    state_def_qn: str,
    *,
    external: list[ForeignArtifact] | None = None,
) -> LfProgram:
    """Build a Lingua Franca program from a SysML state definition.

    Wires the generic :class:`StateMachineDriver` to a
    :class:`RosettaBuilder`.

    Args:
        model: Loaded syside model containing the SysML state def.
        state_def_qn: Qualified name of the SysML ``state def`` to translate.
        external: Optional foreign artifacts providing Python implementations
            for external ``calc def`` calls. The core parses each artifact's
            module name and available function names.

    Returns:
        The assembled ``LfProgram``.
    """
    name = state_def_qn.split("::")[-1]
    needs = PreambleNeeds(SignalCatalog(model, [state_def_qn]))
    needs.types_module = f"{name}_types"
    if external is not None:
        needs.external = external
    result = StateMachineDriver(model).run(
        state_def_qn,
        RosettaBuilder(name, needs=needs, source_qn=state_def_qn),
    )
    assert isinstance(result, LfProgram)
    return finalize(result, needs)
