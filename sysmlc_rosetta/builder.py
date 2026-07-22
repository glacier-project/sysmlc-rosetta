from __future__ import annotations

import logging
from dataclasses import replace

import syside

from sysmlc.backends.rosetta.codegen import (
    LfPythonCodeGen,
    PreambleNeeds,
    files_option,
    py_type,
)
from sysmlc.backends.rosetta.program import (
    Connection,
    Instantiation,
    LfProgram,
    LogicalAction,
    Mode,
    Parameter,
    Reaction,
    Reactor,
    StateVar,
    Timer,
)
from sysmlc.backends.rosetta.serialize import render_duration
from sysmlc.codegen.python import payload_signature
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
    CompositeValue,
    ConstraintFact,
    SignalTrigger,
    StateFact,
    StateKind,
    TransitionFact,
    WhenTrigger,
)
from sysmlc.sysml.queries import feature_value

logger = logging.getLogger(__name__)

OUTPUT_PORT = "current_state"
COMPLETED_PORT = "completed"
DONE_MODE = "done"
CHANGE_ACT = "_change_act"
_PY_INDENT = "    "


def _scope_of(path: str) -> str:
    """The enclosing scope of a state path (``""`` for top-level states)."""
    return path.rpartition("::")[0]


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
    done`` is context-sensitive: the root requests stop, a child sets its
    ``completed`` output, and an eventless transition out of a composite
    fires on that port (completion semantics). Transitions escaping a scope
    raise dedicated ``exit_<k>`` outputs that enclosing scopes resolve or
    re-raise. Capability rejections (deep entry, cross-scope sends,
    ``at``/``when``, non-inline ``do`` bodies, unstable self-loops, name
    collisions) also live here.

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
        """
        self._name = name
        self._peer_accepts = peer_accepts
        self._observe = observe
        self._constraints: list[ConstraintFact] = []
        self._needs = needs if needs is not None else PreambleNeeds()
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
        # scope -> signals whose {sig}_consumed flag this scope's reactor
        #   outputs (set by an inner transition here, or re-emitted from a
        #   child). Empty unless a cross-level priority conflict exists.
        self._consumed: dict[str, dict[str, None]] = {}
        # group-interrupt composite/parallel state -> signal -> boundary child
        #   scope names whose {sig}_consumed flag the group interrupt reads.
        self._guarded_interrupts: dict[str, dict[str, list[str]]] = {}
        # Peer-mode state (empty unless peer_accepts is non-empty):
        # scope -> signals that scope's reactor must output (its own
        # peer-accepted sends plus those of its descendants).
        self._exported: dict[str, dict[str, None]] = {}
        # _port_sigs: machine-level sends that become output ports
        #   (root exports).
        self._port_sigs: frozenset[str] = frozenset()
        # _self_sigs: ported sigs ALSO accepted locally → render both
        #   a port set and a self-event.
        self._self_sigs: frozenset[str] = frozenset()
        # _omitted_inputs: signals dropped from input ports; deliberately
        #   identity-equal to _port_sigs today (named separately because
        #   use sites read "interface rule" vs "codegen rule").
        self._omitted_inputs: frozenset[str] = frozenset()
        # Populated during assembly:
        self._exit_ports: dict[str, dict[str, str]] = {}
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
        self._collect_signals()
        self._collect_consumed()
        self._collect_sends()
        # The port/self signal sets must exist (populated by _collect_sends)
        # before any peer-aware code generator is created.
        self._codegen = self._make_codegen(self._attribute_names)
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
        self_defaulted = self._needs.types_module is None
        if self_defaulted:
            self._needs.types_module = f"{self._name}_types"
        program = LfProgram(
            reactors=tuple(self._reactors),
            preamble=tuple(self._needs.preamble_lines()),
        )
        if self_defaulted:
            program = finalize(program, self._needs, None)
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
            label = fact.name or f"constraint{index}"
            rendered = self._codegen.render_expression(fact.expression)
            if fact.is_negated:
                rendered = f"not ({rendered})"
            checks.append(
                f'assert {rendered}, "SysML constraint {label} violated"'
            )
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
        return replace(machine, reactions=reactions, modes=modes)

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
        return replace(machine, reactions=reactions, modes=modes)

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

    def _collect_signals(self) -> None:
        """Map accepted signals to every scope that must port them.

        ``_handled[s]`` holds signals whose accepting transitions are
        reactions of scope ``s``'s reactor; ``_accepted[s]`` additionally
        includes signals accepted somewhere below ``s`` (the scope's input
        ports, forwarded down by connections).
        """
        for t in self._transitions:
            trigger = t.trigger
            if not isinstance(trigger, SignalTrigger):
                continue
            src_scope = _scope_of(t.source)
            self._handled.setdefault(src_scope, set()).add(trigger.signal_name)
            for scope in _enclosing(src_scope):
                self._accepted.setdefault(scope, {}).setdefault(
                    trigger.signal_name, None
                )

    def _collect_consumed(self) -> None:
        """Plan inner-first plumbing for cross-level priority conflicts.

        A group interrupt on a composite/parallel state ``C`` accepting signal
        ``s``, where ``s`` is also accepted strictly inside ``C``, must yield
        to the inner handler (UML/SCXML inner-first). ``C``'s group-interrupt
        reaction is guarded on a ``{s}_consumed`` flag the descendant raises
        and intermediate scopes re-emit upward, terminating at ``C`` (a
        composite) or at each consuming region (a parallel state).
        """
        for t in self._transitions:
            trigger = t.trigger
            if not isinstance(trigger, SignalTrigger):
                continue
            composite = t.source
            fact = self._facts.get(composite)
            if fact is None or fact.kind is StateKind.LEAF:
                continue  # not a group interrupt on a composite/parallel state
            signal = trigger.signal_name
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
                self._sent_by_scope.setdefault(scope, {}).setdefault(
                    event_name, None
                )
                payload = action.payload_argument
                if isinstance(payload, syside.ConstructorExpression):
                    item_type = payload.instantiated_type
                    if isinstance(item_type, syside.Definition):
                        self._register_dataclass(item_type)
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
        self._self_sigs = frozenset(
            sig
            for sig in self._port_sigs
            if any(sig in handled for handled in self._handled.values())
        )

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
        """Render ``definition`` as a dataclass and register it on the preamble.

        Handles item defs and composite attr defs.  Fields are typed via
        :func:`py_type` and default to the model's declared default, else
        ``None`` (every field defaulted, so the dataclass needs no
        field-ordering care).
        """
        assert definition.name is not None
        lines: list[str] = ["@dataclass", f"class {definition.name}:"]
        attrs = definition.owned_attributes.collect()
        if not attrs:
            lines.append("    pass")
        # NOTE: a field whose type is itself a composite is annotated with
        # that type's name via py_type(), but is NOT recursively registered
        # here. No current model nests composites; revisit if one does.
        for attr in attrs:
            assert attr.name is not None
            default_expr = feature_value(attr)
            if default_expr is None:
                default = "None"
            else:
                try:
                    default = self._init_codegen.render_expression(default_expr)
                except (ValueError, UnsupportedConstructError):
                    # Quantity / complex expressions can't render as plain
                    # Python literals; fall back to None (design note: only
                    # scalar literal defaults are rendered in the companion).
                    default = "None"
            lines.append(f"    {attr.name}: {py_type(attr)} = {default}")
        self._needs.register_dataclass(definition.name, tuple(lines))

    def _render_value(self, value: AttributeValue) -> str | None:
        if value is None:
            return None
        if isinstance(value, CompositeValue):
            self._register_dataclass(value.definition)
            fields = ", ".join(
                f"{name}={self._render_value(field)}"
                for name, field in value.fields
            )
            return f"{value.type_name}({fields})"
        if isinstance(value, float):
            return repr(value)
        # Initial values render with the init codegen (self_prefix=False):
        # they execute outside a reaction, where `self.<attr>` does not
        # exist.
        return self._init_codegen.render_expression(value)

    # -- scope assembly --

    def _reactor_name(self, scope: str) -> str:
        return f"{self._name}_{scope.replace('::', '_')}"

    def _make_codegen(
        self,
        attribute_names: frozenset[str],
        local_names: frozenset[str] = frozenset(),
    ) -> LfPythonCodeGen:
        """Build a peer-aware code generator sharing the preamble registry."""
        return LfPythonCodeGen(
            attribute_names,
            needs=self._needs,
            port_signals=self._port_sigs,
            local_names=local_names,
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
        extra_state: list[StateVar] = []
        modes = [
            self._child_mode(kid, scope, container, sent, gen, extra_state)
            for kid in kids
        ]
        if scope in self._needs_done:
            modes.append(self._done_mode(is_root))
        ported = tuple(self._exported.get(scope, {}))
        consumed = tuple(
            f"{sig}_consumed" for sig in self._consumed.get(scope, {})
        )
        if is_root:
            parameters, state_vars = self._attribute_split()
            outputs: tuple[str, ...] = (OUTPUT_PORT, *ported, *consumed)
            reactions = self._root_reactions(
                container, sent, self._scope_ports("")
            )
            name = self._name
        else:
            parameters, state_vars = [], []
            exit_ports = tuple(self._exit_ports.get(scope, {}).values())
            outputs = (
                COMPLETED_PORT,
                OUTPUT_PORT,
                *exit_ports,
                *ported,
                *consumed,
            )
            reactions = []
            name = self._reactor_name(scope)
        sent_self = [
            sig
            for sig in sent
            if sig not in self._port_sigs or sig in self._self_sigs
        ]
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
        )

    def _parallel_root_reactor(self, root: StateFact) -> Reactor:
        """Assemble a parallel ROOT machine: regions wired at reactor scope.

        The machine has no modes; completion is a join over all regions'
        ``completed`` ports that requests stop.
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
            self._root_reactions(root, sent, self._scope_ports(""))
        )
        flags: list[str] = []
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
            connections += [
                Connection(sig, f"{inst}.{sig}")
                for sig in self._accepted.get(region.name, {})
                if sig not in self._omitted_inputs
            ]
            reactions.append(self._reemit(inst, r_simple, prefix=""))
            reactions += [
                self._port_reemit(inst, sig)
                for sig in self._exported.get(region.name, {})
            ]
            flags.append(f"{r_simple}_done")
        join_body: list[str] = []
        for region, flag in zip(regions, flags, strict=True):
            inst = f"c_{_simple(region.name)}"
            join_body += [
                f"if {inst}.{COMPLETED_PORT}.is_present:",
                f"{_PY_INDENT}self.{flag} = True",
            ]
        condition = " and ".join(f"self.{flag}" for flag in flags)
        join_body += [f"if {condition}:", f"{_PY_INDENT}request_stop()"]
        reactions.append(
            Reaction(
                tuple(f"c_{_simple(r.name)}.{COMPLETED_PORT}" for r in regions),
                (),
                tuple(join_body),
            )
        )
        parameters, state_vars = self._attribute_split()
        state_vars += [StateVar(flag, "False", reset=True) for flag in flags]
        ported = tuple(self._exported.get("", {}))
        sent_self = [
            sig
            for sig in sent
            if sig not in self._port_sigs or sig in self._self_sigs
        ]
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
                signals.setdefault(t.source, set()).add(trigger.signal_name)
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
        # Only state names are checked; attributes shadowing generated
        # identifiers (an attribute named `current_state`) and the mode-local
        # timer/action names are not validated yet.
        reserved = {
            DONE_MODE,
            OUTPUT_PORT,
            COMPLETED_PORT,
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
                    "rosetta generates (done, current_state, completed, a "
                    "signal port/action, or a <state>_fired single-fire flag); "
                    "rename the state."
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
    ) -> Mode:
        simple = _simple(fact.name)
        outgoing = [
            t
            for t in self._scope_transitions.get(scope, [])
            if t.source == fact.name
        ]
        for transition in outgoing:
            if transitions.self_loop_is_unstable(transition):
                raise UnsupportedConstructError(
                    "a self-loop transition would never stabilize (no "
                    "event, timer, or effect to break the loop)."
                )
        eventless: list[TransitionFact] = []
        signal_groups: dict[str, list[TransitionFact]] = {}
        afters: list[TransitionFact] = []
        ats: list[TransitionFact] = []
        whens: list[TransitionFact] = []
        for transition in outgoing:
            trigger = transition.trigger
            if trigger is None:
                eventless.append(transition)
            elif isinstance(trigger, SignalTrigger):
                signal_groups.setdefault(trigger.signal_name, []).append(
                    transition
                )
            elif isinstance(trigger, AfterTrigger):
                afters.append(transition)
            elif isinstance(trigger, AtTrigger):
                ats.append(transition)
            else:
                whens.append(transition)

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
        entry_body += self._statements(fact.entry_action, gen)
        if fact.do_action is not None:
            actions.require_inline_one_shot(fact.do_action)
            entry_body += self._statements(fact.do_action, gen)

        multi = len(signal_groups) + len(afters) + len(ats) + len(whens) >= 2
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
                [transition], exit_stmts, scope, gen, fired=fired_flag
            )
            if isinstance(after, float):
                trigger_name = (
                    f"t_{simple}" if len(afters) == 1 else f"t_{simple}_{index}"
                )
                timers.append(Timer(trigger_name, render_duration(after)))
            else:
                assert after is not None  # AFTER always carries a duration
                trigger_name = (
                    f"after_{simple}_act"
                    if len(afters) == 1
                    else f"after_{simple}_{index}_act"
                )
                mode_actions.append(LogicalAction(trigger_name))
                delay = gen.render_expression(after)
                entry_body.append(
                    f"{trigger_name}.schedule(int(({delay}) * 1e9))"
                )
                entry_effects.append(trigger_name)
            if fired_flag is not None:
                body = [
                    f"if not self.{fired_flag}:",
                    *(f"{_PY_INDENT}{line}" for line in body),
                ]
            ordered.append(
                (
                    pos[id(transition)],
                    self._reaction(
                        (trigger_name,),
                        targets,
                        tuple(body),
                        sent,
                        self._scope_ports(scope),
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
            body, targets = self._dispatch(
                [transition], exit_stmts, scope, gen, fired=fired_flag
            )
            if fired_flag is not None:
                body = [
                    f"if not self.{fired_flag}:",
                    *(f"{_PY_INDENT}{line}" for line in body),
                ]
            ordered.append(
                (
                    pos[id(transition)],
                    self._reaction(
                        (action_name,),
                        targets,
                        tuple(body),
                        sent,
                        self._scope_ports(scope),
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
                [transition], exit_stmts, scope, gen, fired=fired_flag
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
                    self._reaction(
                        (CHANGE_ACT,),
                        targets,
                        tuple(body),
                        sent,
                        self._scope_ports(scope),
                    ),
                )
            )

        for signal, group in signal_groups.items():
            triggers: tuple[str, ...]
            if signal in self._omitted_inputs:
                triggers = (f"{signal}_act",)
            elif signal in sent:
                triggers = (signal, f"{signal}_act")
            else:
                triggers = (signal,)
            payload_names = {
                t.trigger.payload_name
                for t in group
                if isinstance(t.trigger, SignalTrigger)
                and t.trigger.payload_name is not None
            }
            if len(payload_names) > 1:
                raise UnsupportedConstructError(
                    f"transitions on {signal!r} declare different payload "
                    f"names {sorted(payload_names)!r}; use one name."
                )
            prelude: list[str] = []
            group_gen = gen
            if payload_names:
                (payload_name,) = payload_names
                if signal in self._omitted_inputs:
                    value = f"{signal}_act.value"
                elif signal in sent:
                    value = (
                        f"({signal}.value if {signal}.is_present "
                        f"else {signal}_act.value)"
                    )
                else:
                    value = f"{signal}.value"
                prelude = [f"{payload_name} = {value}"]
                group_gen = self._make_codegen(
                    self._scope_attribute_names(scope),
                    local_names=frozenset({payload_name}),
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
                group,
                group_exit,
                scope,
                group_gen,
                consumed=consumed_flag,
                fired=fired_flag,
            )
            guard_srcs = self._interrupt_guard_sources(fact, signal)
            if guard_srcs:
                cond = " or ".join(f"{src}.is_present" for src in guard_srcs)
                body = [
                    f"if not ({cond}):",
                    *(f"{_PY_INDENT}{line}" for line in body),
                ]
                # {sig}_consumed ports are reaction TRIGGERS, not sources/uses,
                # so LF schedules this reaction whenever the flag port fires.
                # This is strictly safe: the flag is only ever set by the child
                # in the same reaction cycle that also sets {sig}, and if the
                # flag somehow arrived alone the body degrades to a no-op
                # (reset() is gated behind ``if not (...is_present)``).
                triggers = (*triggers, *guard_srcs)
            if fired_flag is not None:
                body = [
                    f"if not self.{fired_flag}:",
                    *(f"{_PY_INDENT}{line}" for line in body),
                ]
            ordered.append(
                (
                    min(pos[id(t)] for t in group),
                    self._reaction(
                        triggers,
                        targets,
                        tuple(prelude + body),
                        sent,
                        self._scope_ports(scope),
                    ),
                )
            )

        ordered.sort(key=lambda item: item[0])
        reactions.extend(reaction for _, reaction in ordered)
        if fired_flag is not None:
            entry_body.append(f"self.{fired_flag} = False")

        instantiations: list[Instantiation] = []
        connections: list[Connection] = []

        if fact.kind is StateKind.LEAF:
            if eventless:
                # A guarded eventless self-loop with an effect passes the
                # stability check (quake parity) but re-runs this entry
                # reaction on every reset; it relies on the guard eventually
                # going false.
                body, targets = self._dispatch(
                    eventless, exit_stmts, scope, gen
                )
                entry_body += body
                entry_effects += list(targets)
        elif fact.kind is StateKind.COMPOSITE:
            inst = f"c_{simple}"
            child = self._composite_reactor(fact.name, fact, is_root=False)
            self._reactors.append(child)
            instantiations.append(Instantiation(inst, child.name))
            connections += [
                Connection(sig, f"{inst}.{sig}")
                for sig in self._accepted.get(fact.name, {})
                if sig not in self._omitted_inputs
            ]
            reactions.append(self._reemit(inst, simple, prefix=f"{simple}."))
            reactions += [
                self._port_reemit(inst, sig)
                for sig in self._exported.get(fact.name, {})
            ]
            reactions += [
                self._port_reemit(inst, f"{sig}_consumed")
                for sig in self._consumed.get(fact.name, {})
                if sig in self._consumed.get(scope, {})
            ]
            reactions += self._exit_resolutions(
                inst, fact.name, exit_stmts, scope, sent
            )
            if eventless:
                # Completion semantics: an eventless transition out of a
                # composite fires when the child completes, never on entry.
                body, targets = self._dispatch(
                    eventless, exit_stmts, scope, gen
                )
                reactions.append(
                    self._reaction(
                        (f"{inst}.{COMPLETED_PORT}",),
                        targets,
                        tuple(body),
                        sent,
                        self._scope_ports(scope),
                    )
                )
        else:  # StateKind.PARALLEL
            regions = self._regions(fact)
            all_exit_stmts: list[str] = []
            for region in regions:
                r_simple = _simple(region.name)
                inst = f"c_{r_simple}"
                child = self._composite_reactor(
                    region.name, region, is_root=False
                )
                self._reactors.append(child)
                instantiations.append(Instantiation(inst, child.name))
                connections += [
                    Connection(sig, f"{inst}.{sig}")
                    for sig in self._accepted.get(region.name, {})
                    if sig not in self._omitted_inputs
                ]
                reactions.append(
                    self._reemit(inst, r_simple, prefix=f"{simple}.{r_simple}.")
                )
                reactions += [
                    self._port_reemit(inst, sig)
                    for sig in self._exported.get(region.name, {})
                ]
                reactions += [
                    self._port_reemit(inst, f"{sig}_consumed")
                    for sig in self._consumed.get(region.name, {})
                    if sig in self._consumed.get(scope, {})
                ]
                reactions += self._exit_resolutions(
                    inst, region.name, exit_stmts, scope, sent
                )
                # The region's own entry/do run on parallel-state entry; its
                # exit runs on any transition leaving the parallel state.
                entry_body += self._statements(region.entry_action, gen)
                if region.do_action is not None:
                    actions.require_inline_one_shot(region.do_action)
                    entry_body += self._statements(region.do_action, gen)
                all_exit_stmts += self._statements(region.exit_action, gen)
            all_exit_stmts += exit_stmts
            if eventless:
                flags = [f"{simple}_{_simple(r.name)}_done" for r in regions]
                extra_state += [
                    StateVar(flag, "False", reset=True) for flag in flags
                ]
                entry_body += [f"self.{flag} = False" for flag in flags]
                join_body: list[str] = []
                for region, flag in zip(regions, flags, strict=True):
                    inst = f"c_{_simple(region.name)}"
                    join_body += [
                        f"if {inst}.{COMPLETED_PORT}.is_present:",
                        f"{_PY_INDENT}self.{flag} = True",
                    ]
                body, targets = self._dispatch(
                    eventless, all_exit_stmts, scope, gen
                )
                condition = " and ".join(f"self.{flag}" for flag in flags)
                join_body.append(f"if {condition}:")
                join_body += [f"{_PY_INDENT}{line}" for line in body]
                reactions.append(
                    self._reaction(
                        tuple(
                            f"c_{_simple(r.name)}.{COMPLETED_PORT}"
                            for r in regions
                        ),
                        targets,
                        tuple(join_body),
                        sent,
                        self._scope_ports(scope),
                    )
                )

        entry = self._reaction(
            ("reset", "startup"),
            tuple(entry_effects),
            tuple(entry_body),
            sent,
            self._scope_ports(scope),
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

    def _exit_resolutions(
        self,
        inst: str,
        exited_scope: str,
        exit_stmts: list[str],
        scope: str,
        sent: list[str],
    ) -> list[Reaction]:
        """React to a child's deep-exit ports: resolve or re-raise.

        The inner transition already ran the source state's exit and its
        effect inside the child; this scope adds the exited composite's own
        exit statements and either switches to the (now local) target mode
        or raises its own exit port for the next scope up.
        """
        out: list[Reaction] = []
        for target, port in self._exit_ports.get(exited_scope, {}).items():
            effect, set_line = self._resolve_target(scope, target)
            out.append(
                self._reaction(
                    (f"{inst}.{port}",),
                    (effect,),
                    (*exit_stmts, set_line),
                    sent,
                    self._scope_ports(scope),
                )
            )
        return out

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

    def _reaction(
        self,
        triggers: tuple[str, ...],
        effects: tuple[str, ...],
        body: tuple[str, ...],
        sent: list[str],
        ports: tuple[str, ...] = (),
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
        return Reaction(triggers, all_effects, body)

    def _dispatch(
        self,
        group: list[TransitionFact],
        exit_stmts: list[str],
        scope: str,
        gen: LfPythonCodeGen,
        consumed: str | None = None,
        fired: str | None = None,
    ) -> tuple[list[str], tuple[str, ...]]:
        """Render a same-trigger group as a first-match if/elif dispatch.

        Declaration order is firing priority; a guardless branch always
        fires, so it closes the chain (as ``else`` when guards precede it).
        When ``consumed`` is set (a ``{sig}_consumed`` output), each firing
        branch raises it so an enclosing group interrupt on the same signal
        can yield to this inner transition (UML/SCXML inner-first).

        Args:
            group: The transitions sharing the same trigger.
            exit_stmts: Rendered exit statements of the state being left.
            scope: The assembling scope (resolves targets and exit ports).
            gen: The code generator for guard and statement rendering.
            consumed: The consumption-flag output name, or None.
            fired: A per-mode single-fire flag; each firing branch sets
                `self.<fired> = True`, or None.
        """
        body: list[str] = []
        effects: dict[str, None] = {}
        for index, transition in enumerate(group):
            effect, set_line = self._resolve_target(scope, transition.target)
            effects.setdefault(effect, None)
            branch = (
                exit_stmts
                + self._statements(transition.effect, gen)
                + [set_line]
            )
            if consumed is not None:
                branch = [*branch, f"{consumed}.set(True)"]
            if fired is not None:
                branch = [*branch, f"self.{fired} = True"]
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
        return body, tuple(effects)

    def _done_mode(self, is_root: bool) -> Mode:
        body = [f'{OUTPUT_PORT}.set("{DONE_MODE}")']
        effects = [OUTPUT_PORT]
        if is_root:
            body.append("request_stop()")
        else:
            body.append(f"{COMPLETED_PORT}.set(True)")
            effects.append(COMPLETED_PORT)
        entry = Reaction(("reset", "startup"), tuple(effects), tuple(body))
        return Mode(name=DONE_MODE, reactions=(entry,))

    def _root_reactions(
        self, root: StateFact, sent: list[str], ports: tuple[str, ...] = ()
    ) -> list[Reaction]:
        gen = self._codegen
        body = self._statements(root.entry_action, gen)
        if root.do_action is not None:
            actions.require_inline_one_shot(root.do_action)
            body += self._statements(root.do_action, gen)
        out: list[Reaction] = []
        if body:
            out.append(
                self._reaction(("startup",), (), tuple(body), sent, ports)
            )
        exit_body = self._statements(root.exit_action, gen)
        if exit_body:
            out.append(
                self._reaction(("shutdown",), (), tuple(exit_body), sent, ports)
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
    external: tuple[str, frozenset[str]] | None,
) -> LfProgram:
    """Attach the companion module + ``files:`` to a built program."""
    companion = needs.companion_module_lines()
    file_opt = files_option(
        needs.types_module if companion else None,
        external[0] if external is not None else None,
    )
    options = program.target_options + ((file_opt,) if file_opt else ())
    for event_name, port in sorted(needs.undeliverable_sends):
        logger.warning(
            "send %r via %r has no connected peer; the signal is not "
            "delivered (use `send ... to <own port>` for a self-event)",
            event_name,
            port,
        )
    return replace(
        program,
        target_options=options,
        types_module_name=needs.types_module if companion else None,
        types_module_lines=tuple(companion),
    )


def build_program(
    model: syside.Model,
    state_def_qn: str,
    *,
    external: tuple[str, frozenset[str]] | None = None,
) -> LfProgram:
    """Build a Lingua Franca program from a SysML state definition.

    Wires the generic :class:`StateMachineDriver` to a
    :class:`RosettaBuilder`.

    Args:
        model: Loaded syside model containing the SysML state def.
        state_def_qn: Qualified name of the SysML ``state def`` to translate.
        external: Optional ``(module_stem, names)`` pair identifying a Python
            module that provides external ``calc def`` implementations.

    Returns:
        The assembled ``LfProgram``.
    """
    name = state_def_qn.split("::")[-1]
    needs = PreambleNeeds()
    needs.types_module = f"{name}_types"
    if external is not None:
        needs.register_external(module=external[0], names=external[1])
    result = StateMachineDriver(model).run(
        state_def_qn, RosettaBuilder(name, needs=needs)
    )
    assert isinstance(result, LfProgram)
    return finalize(result, needs, external)
