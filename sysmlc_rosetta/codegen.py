from __future__ import annotations

from typing import TYPE_CHECKING, Final, override

import syside

from sysmlc.codegen.python import PythonCodeGen, payload_signature
from sysmlc.errors import UnsupportedConstructError

if TYPE_CHECKING:
    from sysmlc.codegen.python import PythonCodeGenContext

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
    """Collects everything the generated LF preamble must declare.

    The builder owns one instance and shares it with every code generator;
    rendering registers enum defs, payload item defs, and ``math`` usage as
    they are encountered.
    """

    def __init__(self) -> None:
        self.enum_defs: dict[str, syside.EnumerationDefinition] = {}
        self.item_defs: dict[str, syside.Definition] = {}
        self.uses_math = False

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
    """

    def __init__(
        self,
        attribute_names: frozenset[str],
        context: PythonCodeGenContext | None = None,
        *,
        needs: PreambleNeeds | None = None,
        self_prefix: bool = True,
        local_names: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(context)
        self._attribute_names = attribute_names
        self._needs = needs if needs is not None else PreambleNeeds()
        self._self_prefix = self_prefix
        self._local_names = local_names

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
        """Translate a send into scheduling the signal's logical action.

        Emits ``<Event>_act.schedule(0[, <Event>(<field>=<expr>, ...)])``; the
        zero delay makes the event visible at the next microstep, like an
        internally raised event.  When the payload carries arguments a
        constructor call is emitted (matching the ``@dataclass`` generated in
        the preamble); the no-argument form omits the second argument entirely.

        Args:
            send: The ``send new <Type>(<args>)`` action to translate.

        Returns:
            Python source for the ``schedule(...)`` call.

        Raises:
            ValueError: If the payload is not a ``new <Type>(...)``
                constructor resolving to a named definition, or an argument
                has no corresponding named attribute.
        """
        event_name, pairs = payload_signature(send)
        if not pairs:
            return f"{event_name}_act.schedule(0)"
        args = ", ".join(
            f"{name}={self.render_expression(argument)}"
            for name, argument in pairs
        )
        return f"{event_name}_act.schedule(0, {event_name}({args}))"

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
