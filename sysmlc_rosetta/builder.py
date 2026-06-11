from __future__ import annotations

import syside

from sysmlc.backends.rosetta.codegen import LfPythonCodeGen, PreambleNeeds
from sysmlc.backends.rosetta.program import (
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
DONE_MODE = "done"
_PY_INDENT = "    "


class RosettaBuilder:
    """Assemble a Lingua Franca modal-reactor program from neutral facts.

    Implements the ``TargetBuilder`` protocol. Every LF-specific
    representational choice lives here: states become modes of one reactor,
    signal triggers become input ports (plus a logical action when the
    machine also sends the signal), ``after`` becomes a mode-local timer
    (literal) or a mode-local action scheduled on entry (attribute
    reference), ``then done`` synthesizes a final mode that requests stop,
    and the ``current_state`` output announces every mode entry. Capability
    rejections (hierarchy/parallel, ``at``/``when``, non-inline ``do``
    bodies, unstable self-loops, name collisions) also live here.
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
        self._leaves: list[StateFact] = []
        self._transitions: list[TransitionFact] = []
        self._sent: list[str] = []
        self._needs_namespace = False
        self._needs_done = False

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
        """Buffer a state fact; only the root and its leaf children fit."""
        if state.parent is None:
            self._root = state
            return
        if (
            state.kind is not StateKind.LEAF
            or self._root is None
            or state.parent != self._root.name
        ):
            raise UnsupportedConstructError(
                f"state {state.name!r} is nested, composite, or parallel; "
                "hierarchical and parallel state machines are not yet "
                "supported by rosetta."
            )
        self._leaves.append(state)

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
        if (
            root.kind is not StateKind.COMPOSITE
            or root.initial_substate is None
        ):
            raise UnsupportedConstructError(
                "a parallel root state machine is not yet supported by rosetta."
            )
        self._attribute_names = frozenset(
            binding.name for binding in self._attributes
        )
        self._codegen = LfPythonCodeGen(
            self._attribute_names,
            needs=self._needs,
        )
        parameters, state_vars = self._attribute_split()
        ports = self._signal_ports()
        sent = self._sent_signals(root)
        self._sent = sent
        self._check_names(ports, sent)
        modes = [self._mode(fact, root, sent) for fact in self._leaves]
        if self._needs_done:
            modes.append(self._done_mode())
        reactor = Reactor(
            name=self._name,
            parameters=tuple(parameters),
            inputs=tuple(ports),
            outputs=(OUTPUT_PORT,),
            state_vars=tuple(state_vars),
            actions=tuple(LogicalAction(f"{sig}_act") for sig in sent),
            reactions=tuple(self._root_reactions(root)),
            modes=tuple(modes),
        )
        preamble: list[str] = []
        if self._needs.uses_math:
            preamble.append("import math")
        if self._needs_namespace:
            preamble.append("from types import SimpleNamespace")
        preamble += _enum_classes(self._needs)
        preamble += _payload_classes(self._needs)
        return LfProgram(reactor=reactor, preamble=tuple(preamble))

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

    # -- signal discovery --

    def _signal_ports(self) -> list[str]:
        seen: dict[str, None] = {}
        for transition in self._transitions:
            trigger = transition.trigger
            if trigger is not None and trigger.kind is TriggerKind.SIGNAL:
                assert trigger.signal_name is not None
                seen.setdefault(trigger.signal_name, None)
        return list(seen)

    def _sent_signals(self, root: StateFact) -> list[str]:
        seen: dict[str, None] = {}
        slots: list[syside.ActionUsage | None] = []
        for fact in (root, *self._leaves):
            slots += [fact.entry_action, fact.do_action, fact.exit_action]
        slots += [transition.effect for transition in self._transitions]
        for slot in slots:
            for action in actions.inline_actions(slot):
                if isinstance(action, syside.SendActionUsage):
                    event_name, _pairs = payload_signature(action)
                    seen.setdefault(event_name, None)
                    payload = action.payload_argument
                    if isinstance(payload, syside.ConstructorExpression):
                        item_type = payload.instantiated_type
                        if isinstance(item_type, syside.Definition):
                            self._needs.register_item(item_type)
        return list(seen)

    def _check_names(self, ports: list[str], sent: list[str]) -> None:
        # Only state names are checked; attributes shadowing generated
        # identifiers (an attribute named `current_state`) and the mode-local
        # timer/action names are not validated yet.
        reserved = {
            DONE_MODE,
            OUTPUT_PORT,
            *ports,
            *(f"{sig}_act" for sig in sent),
        }
        for fact in self._leaves:
            if fact.name in reserved:
                raise UnsupportedConstructError(
                    f"state name {fact.name!r} collides with a name rosetta "
                    "generates (done, current_state, or a signal port/"
                    "action); rename the state."
                )

    # -- mode assembly --

    def _mode(self, fact: StateFact, root: StateFact, sent: list[str]) -> Mode:
        outgoing = [t for t in self._transitions if t.source == fact.name]
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

        timers: list[Timer] = []
        mode_actions: list[LogicalAction] = []
        reactions: list[Reaction] = []
        entry_body = [f'{OUTPUT_PORT}.set("{fact.name}")']
        entry_effects = [OUTPUT_PORT]
        entry_body += self._statements(fact.entry_action)
        if fact.do_action is not None:
            actions.require_inline_one_shot(fact.do_action)
            entry_body += self._statements(fact.do_action)

        # Timer/action names are state-qualified: lfc flattens mode-local
        # declarations into one per-reactor C struct, so identical names in
        # two modes collide ("duplicate member" compile errors).
        for index, transition in enumerate(afters):
            assert transition.trigger is not None
            after = transition.trigger.after
            body, targets = self._dispatch([transition], fact)
            if isinstance(after, float):
                trigger_name = (
                    f"t_{fact.name}"
                    if len(afters) == 1
                    else f"t_{fact.name}_{index}"
                )
                timers.append(Timer(trigger_name, render_duration(after)))
            else:
                assert after is not None  # AFTER always carries a duration
                trigger_name = (
                    f"after_{fact.name}_act"
                    if len(afters) == 1
                    else f"after_{fact.name}_{index}_act"
                )
                mode_actions.append(LogicalAction(trigger_name))
                delay = self._codegen.render_expression(after)
                entry_body.append(
                    f"{trigger_name}.schedule(int(({delay}) * 1e9))"
                )
                entry_effects.append(trigger_name)
            reactions.append(
                self._reaction((trigger_name,), targets, tuple(body))
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
            codegen = self._codegen
            if payload_names:
                (payload_name,) = payload_names
                value = (
                    f"({signal}.value if {signal}.is_present "
                    f"else {signal}_act.value)"
                    if signal in sent
                    else f"{signal}.value"
                )
                prelude = [f"{payload_name} = {value}"]
                codegen = LfPythonCodeGen(
                    self._attribute_names,
                    needs=self._needs,
                    local_names=frozenset({payload_name}),
                )
            body, targets = self._dispatch(group, fact, codegen)
            reactions.append(
                self._reaction(triggers, targets, tuple(prelude + body))
            )

        if eventless:
            # A guarded eventless self-loop with an effect passes the
            # stability check (quake parity) but re-runs this entry reaction
            # on every reset; it relies on the guard eventually going false.
            body, targets = self._dispatch(eventless, fact)
            entry_body += body
            entry_effects += list(targets)

        entry = self._reaction(
            ("reset", "startup"),
            tuple(entry_effects),
            tuple(entry_body),
        )
        return Mode(
            name=fact.name,
            initial=(fact.name == root.initial_substate),
            timers=tuple(timers),
            actions=tuple(mode_actions),
            reactions=(entry, *reactions),
        )

    def _reaction(
        self,
        triggers: tuple[str, ...],
        effects: tuple[str, ...],
        body: tuple[str, ...],
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
            for sig in self._sent
            if any(f"{sig}_act.schedule" in line for line in body)
        )
        all_effects = tuple(dict.fromkeys((*effects, *scheduled)))
        return Reaction(triggers, all_effects, body)

    def _dispatch(
        self,
        group: list[TransitionFact],
        fact: StateFact,
        codegen: LfPythonCodeGen | None = None,
    ) -> tuple[list[str], tuple[str, ...]]:
        """Render a same-trigger group as a first-match if/elif dispatch.

        Declaration order is firing priority; a guardless branch always
        fires, so it closes the chain (as ``else`` when guards precede it).

        Args:
            group: The transitions sharing the same trigger.
            fact: The source state fact (for exit-action rendering).
            codegen: The code generator to use for guard and statement
                rendering; defaults to the builder's shared instance.
        """
        gen = codegen if codegen is not None else self._codegen
        body: list[str] = []
        effects: dict[str, None] = {}
        for index, transition in enumerate(group):
            target = self._target_mode(transition.target)
            effects.setdefault(f"reset({target})", None)
            branch = (
                self._statements(fact.exit_action, gen)
                + self._statements(transition.effect, gen)
                + [f"{target}.set()"]
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

    def _target_mode(self, target: str | CompletionTarget) -> str:
        if isinstance(target, CompletionTarget):
            if target.scope:
                raise UnsupportedConstructError(
                    "a nested `then done` cannot occur in a flat machine."
                )
            self._needs_done = True
            return DONE_MODE
        return target

    def _done_mode(self) -> Mode:
        entry = Reaction(
            ("reset", "startup"),
            (OUTPUT_PORT,),
            (f'{OUTPUT_PORT}.set("{DONE_MODE}")', "request_stop()"),
        )
        return Mode(name=DONE_MODE, reactions=(entry,))

    def _root_reactions(self, root: StateFact) -> list[Reaction]:
        body = self._statements(root.entry_action)
        if root.do_action is not None:
            actions.require_inline_one_shot(root.do_action)
            body += self._statements(root.do_action)
        out: list[Reaction] = []
        if body:
            out.append(self._reaction(("startup",), (), tuple(body)))
        exit_body = self._statements(root.exit_action)
        if exit_body:
            out.append(self._reaction(("shutdown",), (), tuple(exit_body)))
        return out

    def _statements(
        self,
        action: syside.ActionUsage | None,
        codegen: LfPythonCodeGen | None = None,
    ) -> list[str]:
        gen = codegen if codegen is not None else self._codegen
        return [
            gen.render_action(candidate)
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
