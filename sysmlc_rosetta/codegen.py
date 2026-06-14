from __future__ import annotations

from typing import TYPE_CHECKING, Final, override

import syside

from sysmlc.codegen.python import PythonCodeGen, payload_signature
from sysmlc.errors import UnsupportedConstructError

if TYPE_CHECKING:
    from sysmlc.codegen.python import PythonCodeGenContext

# ---------------------------------------------------------------------------
# Verified syside node shapes for call-effect transitions (2026-06-15)
# Fixture: models/sm-examples/sm14-call-effect/sm14.sysml
#
# NOTE: rosetta uses ONLY the assignment-from-call form below. print/log are
# functions (not actions), so the perform-call form is kept for reference but
# is NOT generated. See the RESHAPE NOTE in docs/rosetta-functions-plan.md.
#
# Perform-call effect (do logAct where logAct : sysmlc::log { ... }):
#   t.effect                      -> PerformActionUsage
#   t.effect.performed_action     -> ActionUsage (the named logAct usage)
#   performed_action.owned_typings[].type
#                                 -> ActionDefinition  qn=sysmlc::log
#   arg bindings on performed_action.owned_features (ReferenceUsage):
#     feature.feature_value.value -> LiteralString (.value) or
#                                    FeatureReferenceExpression (.referent)
#   actions.inline_actions(eff)   -> []  (PerformActionUsage not unwrapped)
#
# Assignment-from-calc (do assign theta := Plant::step(theta, 0.1)):
#   t.effect                      -> AssignmentActionUsage
#   inline_actions(t.effect)[0]   -> AssignmentActionUsage
#   act.value_expression          -> InvocationExpression
#   act.value_expression.function -> CalculationDefinition  (not ActionDef)
#   act.value_expression.arguments[]  (positional):
#       -> [FeatureReferenceExpression, LiteralRational, …]
#   FeatureReferenceExpression.referent -> AttributeUsage (qn resolved)
# ---------------------------------------------------------------------------

# Maps a fully-qualified SysML function name to the Python call target and a
# flag indicating whether the generated code needs ``import math``.
_FUNCTIONS: Final[dict[str, tuple[str, bool]]] = {
    # qualified SysML function -> (python call target, needs math import)
    "NumericalFunctions::abs": ("abs", False),
    "NumericalFunctions::max": ("max", False),
    "NumericalFunctions::min": ("min", False),
    "TrigFunctions::sin": ("math.sin", True),
    "TrigFunctions::cos": ("math.cos", True),
    "TrigFunctions::tan": ("math.tan", True),
}


class PreambleNeeds:
    """Collects and renders everything the generated LF preamble declares.

    The builder owns one instance and shares it with every code generator;
    rendering registers enum defs, payload item defs, and ``math`` /
    ``SimpleNamespace`` usage as they are encountered, and
    :meth:`preamble_lines` assembles the preamble source lines from them.
    """

    def __init__(self) -> None:
        self.enum_defs: dict[str, syside.EnumerationDefinition] = {}
        self.item_defs: dict[str, syside.Definition] = {}
        self.uses_math = False
        self.uses_namespace = False
        self.external_module: str | None = None
        self.external_names: frozenset[str] = frozenset()
        self.used_external: set[str] = set()

    def register_enum(self, literal: syside.EnumerationUsage) -> str:
        """Register the literal's enum def; return ``Def.literal`` source.

        Args:
            literal: An ``EnumerationUsage`` node representing one enum
                literal (e.g. ``LightColor::red``).

        Returns:
            Python source for the literal, e.g. ``LightColor.red``.

        Raises:
            UnsupportedConstructError: If the literal is not owned by an
                ``EnumerationDefinition``, or if two different definitions
                share the same simple name.
        """
        owner = literal.owner
        if not isinstance(owner, syside.EnumerationDefinition):
            raise UnsupportedConstructError(
                "enum literal is not owned by an enumeration definition",
                node=literal,
            )
        assert owner.name is not None and literal.name is not None
        known = self.enum_defs.get(owner.name)
        if known is not None and known != owner:
            raise UnsupportedConstructError(
                f"two enum definitions share the simple name {owner.name!r};"
                " rename one.",
                node=owner,
            )
        self.enum_defs[owner.name] = owner
        return f"{owner.name}.{literal.name}"

    def register_item(self, item: syside.Definition) -> None:
        """Register a sent item def for payload-class generation.

        Raises:
            UnsupportedConstructError: If two different definitions share
                the same simple name.
        """
        assert item.name is not None
        known = self.item_defs.get(item.name)
        if known is not None and known != item:
            raise UnsupportedConstructError(
                f"two item definitions share the simple name {item.name!r};"
                " rename one.",
                node=item,
            )
        self.item_defs[item.name] = item

    def register_external(self, *, module: str, names: frozenset[str]) -> None:
        """Record the --python module and the function names it provides."""
        self.external_module = module
        self.external_names = names

    def _enum_class_lines(self) -> list[str]:
        """Render registered enum defs as Python Enum classes, sorted by name.

        Returns:
            Lines of Python source: an ``from enum import Enum`` header (when
            any enums are registered) followed by one class block per enum.
        """
        lines: list[str] = []
        for name in sorted(self.enum_defs):
            lines.append(f"class {name}(Enum):")
            for literal in self.enum_defs[name].owned_members.collect():
                if not isinstance(literal, syside.EnumerationUsage):
                    continue  # an enum def may own non-literal members
                assert literal.name is not None
                lines.append(f'    {literal.name} = "{literal.name}"')
        if lines:
            lines.insert(0, "from enum import Enum")
        return lines

    def _payload_class_lines(self) -> list[str]:
        """Render registered (sent) item defs as payload dataclasses.

        Returns:
            Lines of Python source: a ``from dataclasses import dataclass``
            header (when any items are registered) followed by one
            ``@dataclass`` class block per item def, sorted by name.
        """
        lines: list[str] = []
        for name in sorted(self.item_defs):
            attrs = self.item_defs[name].owned_attributes.collect()
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

    def preamble_lines(self) -> list[str]:
        """Assemble the LF preamble: imports, enum classes, payloads.

        Imports precede the class blocks that rely on them.  Line order is
        pinned by golden tests: math import, SimpleNamespace import,
        external-module imports, enum classes, payload dataclasses.
        """
        lines: list[str] = []
        if self.uses_math:
            lines.append("import math")
        if self.uses_namespace:
            lines.append("from types import SimpleNamespace")
        for name in sorted(self.used_external):
            assert self.external_module is not None
            lines.append(f"from {self.external_module} import {name}")
        lines += self._enum_class_lines()
        lines += self._payload_class_lines()
        return lines


class LfPythonCodeGen(PythonCodeGen):
    """Python code generator for Lingua Franca reaction bodies.

    Reaction bodies run as methods of the generated reactor, so attribute
    references render as ``self.<name>``. Only the state definition's bound
    attributes are valid names: anything else (in particular an accept
    payload parameter) fails loud rather than generating broken code.

    For chained references (``a.b``), the base feature is prefixed with
    ``self.`` automatically: the chain handler in the base class calls
    ``self._emit(operands[0])``, which dispatches back into the overridden
    ``_emit_feature_reference``, so ``self.pt.x`` falls out without
    overriding the chain handler.

    Enum literals (``LightColor::red``) are resolved via ``PreambleNeeds``:
    the first encounter registers the owning ``EnumerationDefinition`` and
    returns the ``Def.literal`` form expected by Python ``Enum``.

    Args:
        attribute_names: Simple names of the machine's bound attributes.
        context: A PythonCodeGenContext instance, or None for the default.
        needs: Registry that collects preamble requirements (enum classes,
            payload dataclasses, ``math`` imports).  When ``None`` a fresh
            private registry is created; pass the builder's shared instance
            so registrations are visible when assembling the preamble.
        self_prefix: When ``False`` the ``self.`` prefix is suppressed and
            the attribute-name check is skipped.  Used by the init codegen
            that renders outside of any reaction method.
        local_names: Names that are in scope as plain locals (e.g. accept
            payload parameters) and must not receive a ``self.`` prefix.
        port_signals: Sent signals that a peer machine accepts, so the send
            sets an LF output port instead of (or alongside) scheduling a
            self-event.
        self_signals: Port signals (subset of ``port_signals``) that this
            machine also accepts itself, so the send both sets the port and
            schedules the self-event.
    """

    def __init__(
        self,
        attribute_names: frozenset[str],
        context: PythonCodeGenContext | None = None,
        *,
        needs: PreambleNeeds | None = None,
        self_prefix: bool = True,
        local_names: frozenset[str] = frozenset(),
        port_signals: frozenset[str] = frozenset(),
        self_signals: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(context)
        self._attribute_names = attribute_names
        self._needs = needs if needs is not None else PreambleNeeds()
        self._self_prefix = self_prefix
        self._local_names = local_names
        self._port_signals = port_signals
        self._self_signals = self_signals

    @override
    def _emit_feature_reference(
        self, expr: syside.FeatureReferenceExpression
    ) -> str:
        """Emit a feature reference as the appropriate Python source.

        Three reference kinds are handled:

        - **Enum literal** (referent is an ``EnumerationUsage``): the
          owning definition is registered in ``PreambleNeeds`` and the
          literal is returned as ``DefName.literal``.
        - **Local name** (in ``local_names``): returned bare, no prefix.
        - **Attribute** (in ``attribute_names``): returned as
          ``self.<name>`` when ``self_prefix`` is ``True``; otherwise bare.

        Args:
            expr: The feature reference to translate.

        Returns:
            Python source for ``expr``.

        Raises:
            UnsupportedConstructError: If the referent is not an enum
                literal, not a local name, and not a bound attribute.
            ValueError: If the referent has no resolved name.
        """
        ref = expr.referent
        if isinstance(ref, syside.EnumerationUsage):
            return self._needs.register_enum(ref)
        name = super()._emit_feature_reference(expr)
        if name in self._local_names:
            return name
        if not self._self_prefix:
            return name
        self._require_attribute(name, expr)
        return f"self.{name}"

    @override
    def render_assignment_target(
        self, assign: syside.AssignmentActionUsage
    ) -> str:
        """Emit an assignment target as ``self.<target>``.

        Only the base segment is validated against the attribute set; tail
        segments (the ``x`` in ``pt.x``) are fields of a composite attribute
        and cannot be checked here.

        Args:
            assign: The ``assign <target> := <expr>`` action to inspect.

        Returns:
            Python source for the assignment target, e.g. ``self.counter``
            or ``self.equipment.flag``.

        Raises:
            UnsupportedConstructError: If the base feature name is not in
                the bound attribute set.
            ValueError: If the assignment target has no resolved feature.
        """
        target = super().render_assignment_target(assign)
        self._require_attribute(target.split(".")[0], assign)
        return f"self.{target}"

    @override
    def render_send(self, send: syside.SendActionUsage) -> str:
        """Translate a send into a port set, a self-event, or both.

        Three forms are emitted, selected by the signal sets:

        - **Default** (signal not in ``port_signals``): schedule the
          signal's logical action, ``<Event>_act.schedule(0[, <Event>(...)])``;
          the zero delay makes the event visible at the next microstep, like
          an internally raised event.
        - **Port-only** (signal in ``port_signals`` but not
          ``self_signals``): a peer accepts the signal and this machine does
          not, so set the LF output port, ``<Event>.set(<payload or True>)``.
        - **Overlap** (signal in both sets): set the port *and* schedule the
          self-event, since the signal is both ported to a peer and accepted
          locally.

        When the payload carries arguments a constructor call is emitted
        (matching the ``@dataclass`` generated in the preamble); the
        no-argument form sets the port to ``True`` and omits the schedule's
        second argument entirely.

        Inside an LF reaction a ported signal's output port appears as a
        parameter named ``<Event>``, which shadows the module-level preamble
        dataclass of the same name.  Calling ``<Event>(...)`` there hits the
        port capsule, not the class, raising ``TypeError`` at runtime.  So on
        the ported paths (port-only and overlap) the payload constructor is
        reached via ``globals()["<Event>"](...)``.  The default path is
        unaffected (its parameter is ``<Event>_act``) and keeps the plain
        constructor, preserving byte-identity for bare builds.

        Args:
            send: The ``send new <Type>(<args>)`` action to translate.

        Returns:
            Python source for the resulting statement(s); the overlap form is
            two lines separated by a newline.

        Raises:
            ValueError: If the payload is not a ``new <Type>(...)``
                constructor resolving to a named definition, or an argument
                has no corresponding named attribute.
        """
        event_name, pairs = payload_signature(send)
        args = ", ".join(
            f"{name}={self.render_expression(argument)}"
            for name, argument in pairs
        )
        payload = f"{event_name}({args})" if pairs else None
        schedule = (
            f"{event_name}_act.schedule(0, {payload})"
            if payload
            else f"{event_name}_act.schedule(0)"
        )
        if event_name not in self._port_signals:
            return schedule
        # On ported paths `{event_name}` is the reaction's port parameter,
        # shadowing the preamble class; reach the class through globals().
        ported_payload = f'globals()["{event_name}"]({args})' if pairs else None
        set_line = (
            f"{event_name}.set({ported_payload if ported_payload else 'True'})"
        )
        if event_name in self._self_signals:
            ported_schedule = (
                f"{event_name}_act.schedule(0, {ported_payload})"
                if ported_payload
                else schedule
            )
            return f"{set_line}\n{ported_schedule}"
        return set_line

    @override
    def _emit(self, expr: syside.Expression, parent_precedence: int = 0) -> str:
        """Dispatch, additionally handling whitelisted function calls.

        ``OperatorExpression`` is a subclass of ``InvocationExpression`` in
        syside, so the guard must exclude it explicitly to avoid shadowing the
        base class's operator handler.  That exclusion also covers
        ``FeatureChainExpression`` (a subclass of ``OperatorExpression``).
        ``ConstructorExpression`` shares the same parent
        (``InstantiationExpression``) but is *not* a subclass of
        ``InvocationExpression``, so no exclusion is required for it.
        """
        if isinstance(expr, syside.InvocationExpression) and not isinstance(
            expr, syside.OperatorExpression
        ):
            return self._emit_invocation(expr)
        return super()._emit(expr, parent_precedence)

    def _emit_invocation(self, expr: syside.InvocationExpression) -> str:
        """Emit a whitelisted function call; reject anything else.

        A call is an atom (no precedence wrapping needed).

        Args:
            expr: The ``InvocationExpression`` node to translate.

        Returns:
            Python source for the call, e.g. ``abs(self.x)`` or
            ``math.cos(self.x)``.

        Raises:
            UnsupportedConstructError: If the invoked function is not in
                rosetta's supported set.
        """
        func = expr.function
        qn = None if func is None else func.qualified_name
        if qn is None or str(qn) not in _FUNCTIONS:
            if (
                isinstance(func, syside.CalculationDefinition)
                and func.name is not None
                and func.name in self._needs.external_names
            ):
                self._needs.used_external.add(func.name)
                args = ", ".join(
                    self._emit(argument, 0)
                    for argument in expr.arguments.collect()
                )
                return f"{func.name}({args})"
            raise UnsupportedConstructError(
                f"function {qn or '<unresolved>'!s} is not in rosetta's "
                "supported set.",
                node=expr,
            )
        target, needs_math = _FUNCTIONS[str(qn)]
        if needs_math:
            self._needs.uses_math = True
        args = ", ".join(
            self._emit(argument, 0) for argument in expr.arguments.collect()
        )
        return f"{target}({args})"

    def _require_attribute(self, name: str, node: syside.Element) -> None:
        if name not in self._attribute_names:
            raise UnsupportedConstructError(
                f"reference to {name!r} does not resolve to a bound "
                "attribute; referencing an accept payload or any other "
                "non-attribute feature is not supported by rosetta yet.",
                node=node,
            )
