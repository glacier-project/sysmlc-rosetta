from __future__ import annotations

import syside

from sysmlc.backends.rosetta.codegen import LfPythonCodeGen, PreambleNeeds
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
    AttributeBinding,
    AttributeDirection,
    AttributeValue,
    CompletionTarget,
    CompositeValue,
    StateFact,
    StateKind,
    TransitionFact,
    TriggerKind,
)

OUTPUT_PORT = "current_state"
COMPLETED_PORT = "completed"
DONE_MODE = "done"
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
    """

    def __init__(self, name: str) -> None:
        """Initialize the builder.

        Args:
            name: The reactor name (the state definition's name).
        """
        self._name = name
        self._needs = PreambleNeeds()
        self._init_codegen = LfPythonCodeGen(
            frozenset(), needs=self._needs, self_prefix=False
        )
        # Re-created in result() once the bound attribute names are known.
        self._codegen = LfPythonCodeGen(frozenset(), needs=self._needs)
        self._attributes: list[AttributeBinding] = []
        self._attribute_names: frozenset[str] = frozenset()
        self._root: StateFact | None = None
        self._facts: dict[str, StateFact] = {}
        self._transitions: list[TransitionFact] = []
        self._needs_namespace = False
        # Populated by result() before assembly:
        self._children: dict[str, list[StateFact]] = {}
        self._scope_transitions: dict[str, list[TransitionFact]] = {}
        self._accepted: dict[str, dict[str, None]] = {}
        self._handled: dict[str, set[str]] = {}
        self._sent_by_scope: dict[str, dict[str, None]] = {}
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
        self._codegen = LfPythonCodeGen(
            self._attribute_names,
            needs=self._needs,
        )
        for fact in self._facts.values():
            self._children.setdefault(_scope_of(fact.name), []).append(fact)
        self._classify_transitions()
        self._collect_signals()
        self._collect_sends()
        if root.kind is StateKind.PARALLEL:
            machine = self._parallel_root_reactor(root)
        elif root.kind is StateKind.COMPOSITE:
            machine = self._composite_reactor("", root, is_root=True)
        else:
            raise UnsupportedConstructError(
                "the state definition declares no substates."
            )
        self._reactors.append(machine)
        preamble: list[str] = []
        if self._needs.uses_math:
            preamble.append("import math")
        if self._needs_namespace:
            preamble.append("from types import SimpleNamespace")
        preamble += _enum_classes(self._needs)
        preamble += _payload_classes(self._needs)
        return LfProgram(
            reactors=tuple(self._reactors), preamble=tuple(preamble)
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

    def _collect_signals(self) -> None:
        """Map accepted signals to every scope that must port them.

        ``_handled[s]`` holds signals whose accepting transitions are
        reactions of scope ``s``'s reactor; ``_accepted[s]`` additionally
        includes signals accepted somewhere below ``s`` (the scope's input
        ports, forwarded down by connections).
        """
        for t in self._transitions:
            trigger = t.trigger
            if trigger is None or trigger.kind is not TriggerKind.SIGNAL:
                continue
            assert trigger.signal_name is not None
            src_scope = _scope_of(t.source)
            self._handled.setdefault(src_scope, set()).add(trigger.signal_name)
            for scope in _enclosing(src_scope):
                self._accepted.setdefault(scope, {}).setdefault(
                    trigger.signal_name, None
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
                        self._needs.register_item(item_type)
        for scope, sent in self._sent_by_scope.items():
            for sig in sent:
                for other, handled in self._handled.items():
                    if other != scope and sig in handled:
                        raise UnsupportedConstructError(
                            f"signal {sig!r} is sent and accepted in "
                            "different composite scopes; cross-scope events "
                            "are not supported by rosetta."
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

    def _render_value(self, value: AttributeValue) -> str | None:
        if value is None:
            return None
        if isinstance(value, CompositeValue):
            self._needs_namespace = True
            fields = ", ".join(
                f"{name}={self._render_value(field)}"
                for name, field in value.fields
            )
            return f"SimpleNamespace({fields})"
        if isinstance(value, float):
            return repr(value)
        # Initial values render with the init codegen (self_prefix=False):
        # they execute outside a reaction, where `self.<attr>` does not
        # exist.
        return self._init_codegen.render_expression(value)

    # -- scope assembly --

    def _reactor_name(self, scope: str) -> str:
        return f"{self._name}_{scope.replace('::', '_')}"

    def _scope_codegen(self, scope: str) -> LfPythonCodeGen:
        """The expression codegen for a scope's reaction bodies.

        Child scopes see NO attribute names: substate guards/actions
        referencing root-scope attributes fail loudly in codegen (the
        documented slice-6 scope limit).
        """
        if scope == "":
            return self._codegen
        return LfPythonCodeGen(frozenset(), needs=self._needs)

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
        inputs = list(self._accepted.get(scope, {}))
        gen = self._scope_codegen(scope)
        self._check_names(scope, kids, inputs, sent)
        extra_state: list[StateVar] = []
        modes = [
            self._child_mode(kid, scope, container, sent, gen, extra_state)
            for kid in kids
        ]
        if scope in self._needs_done:
            modes.append(self._done_mode(is_root))
        if is_root:
            parameters, state_vars = self._attribute_split()
            outputs: tuple[str, ...] = (OUTPUT_PORT,)
            reactions = self._root_reactions(container, sent)
            name = self._name
        else:
            parameters, state_vars = [], []
            exit_ports = tuple(self._exit_ports.get(scope, {}).values())
            outputs = (COMPLETED_PORT, OUTPUT_PORT, *exit_ports)
            reactions = []
            name = self._reactor_name(scope)
        return Reactor(
            name=name,
            parameters=tuple(parameters),
            inputs=tuple(inputs),
            outputs=outputs,
            state_vars=tuple(state_vars) + tuple(extra_state),
            actions=tuple(LogicalAction(f"{sig}_act") for sig in sent),
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
        inputs = list(self._accepted.get("", {}))
        self._check_names("", regions, inputs, sent)
        instantiations: list[Instantiation] = []
        connections: list[Connection] = []
        reactions: list[Reaction] = list(self._root_reactions(root, sent))
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
            ]
            reactions.append(self._reemit(inst, r_simple, prefix=""))
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
        state_vars += [StateVar(flag, "False") for flag in flags]
        return Reactor(
            name=self._name,
            parameters=tuple(parameters),
            inputs=tuple(inputs),
            outputs=(OUTPUT_PORT,),
            state_vars=tuple(state_vars),
            actions=tuple(LogicalAction(f"{sig}_act") for sig in sent),
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
            *(f"{sig}_act" for sig in sent),
            *(f"c_{_simple(kid.name)}" for kid in kids),
        }
        for fact in kids:
            if _simple(fact.name) in reserved:
                raise UnsupportedConstructError(
                    f"state name {_simple(fact.name)!r} collides with a name "
                    "rosetta generates (done, current_state, completed, or a "
                    "signal port/action); rename the state."
                )

    # -- mode assembly --

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
        for transition in outgoing:
            trigger = transition.trigger
            if trigger is None:
                eventless.append(transition)
            elif trigger.kind is TriggerKind.SIGNAL:
                assert trigger.signal_name is not None
                signal_groups.setdefault(trigger.signal_name, []).append(
                    transition
                )
            elif trigger.kind is TriggerKind.AFTER:
                afters.append(transition)
            else:
                raise UnsupportedConstructError(
                    f"an `accept {trigger.kind.name.lower()}` trigger is "
                    "unsupported by rosetta; only signal and relative "
                    "`accept after` triggers are supported."
                )

        exit_stmts = self._statements(fact.exit_action, gen)
        timers: list[Timer] = []
        mode_actions: list[LogicalAction] = []
        reactions: list[Reaction] = []
        entry_body = [f'{OUTPUT_PORT}.set("{simple}")']
        entry_effects = [OUTPUT_PORT]
        entry_body += self._statements(fact.entry_action, gen)
        if fact.do_action is not None:
            actions.require_inline_one_shot(fact.do_action)
            entry_body += self._statements(fact.do_action, gen)

        # Timer/action names are state-qualified: lfc flattens mode-local
        # declarations into one per-reactor C struct, so identical names in
        # two modes collide ("duplicate member" compile errors).
        for index, transition in enumerate(afters):
            assert transition.trigger is not None
            after = transition.trigger.after
            body, targets = self._dispatch([transition], exit_stmts, scope, gen)
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
            reactions.append(
                self._reaction((trigger_name,), targets, tuple(body), sent)
            )

        for signal, group in signal_groups.items():
            triggers = (
                (signal, f"{signal}_act") if signal in sent else (signal,)
            )
            payload_names = {
                t.trigger.payload_name
                for t in group
                if t.trigger is not None and t.trigger.payload_name is not None
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
                value = (
                    f"({signal}.value if {signal}.is_present "
                    f"else {signal}_act.value)"
                    if signal in sent
                    else f"{signal}.value"
                )
                prelude = [f"{payload_name} = {value}"]
                group_gen = LfPythonCodeGen(
                    self._scope_attribute_names(scope),
                    needs=self._needs,
                    local_names=frozenset({payload_name}),
                )
            group_exit = self._statements(fact.exit_action, group_gen)
            body, targets = self._dispatch(group, group_exit, scope, group_gen)
            reactions.append(
                self._reaction(triggers, targets, tuple(prelude + body), sent)
            )

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
            ]
            reactions.append(self._reemit(inst, simple, prefix=f"{simple}."))
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
                ]
                reactions.append(
                    self._reemit(inst, r_simple, prefix=f"{simple}.{r_simple}.")
                )
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
                extra_state += [StateVar(flag, "False") for flag in flags]
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
                    )
                )

        entry = self._reaction(
            ("reset", "startup"),
            tuple(entry_effects),
            tuple(entry_body),
            sent,
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

    def _reaction(
        self,
        triggers: tuple[str, ...],
        effects: tuple[str, ...],
        body: tuple[str, ...],
        sent: list[str],
    ) -> Reaction:
        """Build a reaction, declaring scheduled send actions as effects.

        lfc's Python target passes only the names listed in the effects
        clause into the reaction function, so every reactor-level logical
        action the body schedules must be declared as an effect.
        """
        # Substring scan over our own codegen output; a signal name that is
        # a suffix of another ("Tick"/"RetryTick") may add a spurious effect,
        # which LF treats as benign (the action is simply never triggered).
        scheduled = (
            f"{sig}_act"
            for sig in sent
            if any(f"{sig}_act.schedule" in line for line in body)
        )
        all_effects = tuple(dict.fromkeys((*effects, *scheduled)))
        return Reaction(triggers, all_effects, body)

    def _dispatch(
        self,
        group: list[TransitionFact],
        exit_stmts: list[str],
        scope: str,
        gen: LfPythonCodeGen,
    ) -> tuple[list[str], tuple[str, ...]]:
        """Render a same-trigger group as a first-match if/elif dispatch.

        Declaration order is firing priority; a guardless branch always
        fires, so it closes the chain (as ``else`` when guards precede it).

        Args:
            group: The transitions sharing the same trigger.
            exit_stmts: Rendered exit statements of the state being left.
            scope: The assembling scope (resolves targets and exit ports).
            gen: The code generator for guard and statement rendering.
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
        self, root: StateFact, sent: list[str]
    ) -> list[Reaction]:
        gen = self._codegen
        body = self._statements(root.entry_action, gen)
        if root.do_action is not None:
            actions.require_inline_one_shot(root.do_action)
            body += self._statements(root.do_action, gen)
        out: list[Reaction] = []
        if body:
            out.append(self._reaction(("startup",), (), tuple(body), sent))
        exit_body = self._statements(root.exit_action, gen)
        if exit_body:
            out.append(
                self._reaction(("shutdown",), (), tuple(exit_body), sent)
            )
        return out

    def _statements(
        self,
        action: syside.ActionUsage | None,
        codegen: LfPythonCodeGen,
    ) -> list[str]:
        return [
            codegen.render_action(candidate)
            for candidate in actions.inline_actions(action)
        ]


def _enum_classes(needs: PreambleNeeds) -> list[str]:
    """Render registered enum defs as Python Enum classes, sorted by name.

    Args:
        needs: The preamble registry populated during code generation.

    Returns:
        Lines of Python source: an ``from enum import Enum`` header (when
        any enums are registered) followed by one class block per enum.
    """
    lines: list[str] = []
    for name in sorted(needs.enum_defs):
        lines.append(f"class {name}(Enum):")
        for literal in needs.enum_defs[name].owned_members.collect():
            if not isinstance(literal, syside.EnumerationUsage):
                continue  # an enum def may own non-literal members
            assert literal.name is not None
            lines.append(f'    {literal.name} = "{literal.name}"')
    if lines:
        lines.insert(0, "from enum import Enum")
    return lines


def _payload_classes(needs: PreambleNeeds) -> list[str]:
    """Render registered (sent) item defs as payload dataclasses.

    Args:
        needs: The preamble registry populated during code generation.

    Returns:
        Lines of Python source: a ``from dataclasses import dataclass``
        header (when any items are registered) followed by one
        ``@dataclass`` class block per item def, sorted by name.
    """
    lines: list[str] = []
    for name in sorted(needs.item_defs):
        attrs = needs.item_defs[name].owned_attributes.collect()
        lines.append("@dataclass")
        lines.append(f"class {name}:")
        if not attrs:
            lines.append("    pass")
        for attr in attrs:
            assert attr.name is not None
            # Fields are intentionally untyped (payloads are duck-typed;
            # SysML scalar types are not mapped to Python types yet).
            lines.append(f"    {attr.name}: object = None")
    if lines:
        lines.insert(0, "from dataclasses import dataclass")
    return lines


def build_program(model: syside.Model, state_def_qn: str) -> LfProgram:
    """Build a Lingua Franca program from a SysML state definition.

    Wires the generic :class:`StateMachineDriver` to a
    :class:`RosettaBuilder`.

    Args:
        model: Loaded syside model containing the SysML state def.
        state_def_qn: Qualified name of the SysML ``state def`` to translate.

    Returns:
        The assembled ``LfProgram``.
    """
    name = state_def_qn.split("::")[-1]
    result = StateMachineDriver(model).run(state_def_qn, RosettaBuilder(name))
    assert isinstance(result, LfProgram)
    return result
