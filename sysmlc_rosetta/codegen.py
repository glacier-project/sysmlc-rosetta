from __future__ import annotations

from typing import TYPE_CHECKING, override

import syside
from sysmlc.codegen.python import (
    PythonCodeGen,
    payload_fields,
    payload_signature,
)
from sysmlc.codegen.structured import (
    DataclassRegistry,
    constructed_payload_definition,
    types_import_lines,
)
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.statemachine.interface import (
    send_receiver_is_own_port,
    send_via_port,
)

if TYPE_CHECKING:
    from sysmlc.codegen.python import PythonCodeGenContext
    from sysmlc.semantics.statemachine.signals import SignalCatalog
    from sysmlc.sysml.foreign_artifact.base import ForeignArtifact

    from sysmlc_rosetta.program import LfConstraint

# ---------------------------------------------------------------------------
# Verified syside node shapes for call-effect transitions (2026-06-15)
# Fixture: sm-examples/sm14-call-effect/sm14.sysml (sysmlc-models corpus)
#
# NOTE: rosetta uses ONLY the assignment-from-call form below. print/log
# are functions (not actions); a bare perform-call is invalid syside, so
# the perform-call shapes below are a historical probe finding (since
# removed from the fixture), NOT generated.
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
# Assignment-from-calc (do assign x := P::step(x, 0.1)):
#   t.effect                      -> AssignmentActionUsage
#   inline_actions(t.effect)[0]   -> AssignmentActionUsage
#   act.value_expression          -> InvocationExpression
#   act.value_expression.function -> CalculationDefinition  (not ActionDef)
#   act.value_expression.arguments[]  (positional):
#       -> [FeatureReferenceExpression, LiteralRational, …]
#   FeatureReferenceExpression.referent -> AttributeUsage (qn resolved)
# ---------------------------------------------------------------------------


class PreambleNeeds:
    """Collects and renders everything the generated LF preamble declares.

    The builder owns one instance and shares it with every code generator;
    rendering registers enum defs, dataclass blocks, and ``math`` usage as
    they are encountered, and :meth:`preamble_lines` assembles the preamble
    import lines from them (type definitions go to the companion module via
    :meth:`companion_module_lines`).

    Source identities for generated constraint checks are retained for
    consumers of the assembled program.
    """

    def __init__(self, catalog: SignalCatalog | None = None) -> None:
        self.catalog = catalog
        self.enum_defs: dict[str, syside.EnumerationDefinition] = {}
        self.dataclasses = DataclassRegistry(
            symbols=catalog.definition_symbols if catalog else None
        )
        self.uses_math = False
        self.uses_logging = False
        self.types_module: str | None = None
        self.external: list[ForeignArtifact] = []
        self.constraints: dict[tuple[str, int], LfConstraint] = {}
        self._used_external: dict[ForeignArtifact, set[str]] = {}
        # (event_name, via_port) for `via` sends with no connected peer;
        # surfaced as build warnings (see builder.finalize).
        self.undeliverable_sends: set[tuple[str, str]] = set()

    @property
    def used_external(self) -> dict[ForeignArtifact, set[str]]:
        """Foreign artifacts and function names actually invoked so far.

        Populated incrementally as calc-def calls backed by an external
        artifact are emitted. Callers (typically a backend that needs to
        know which support modules to bundle) should read this only after
        generation is complete.
        """
        return self._used_external

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
        symbol = (
            self.catalog.definition_symbols[str(owner.qualified_name)]
            if self.catalog
            else owner.name
        )
        known = self.enum_defs.get(symbol)
        if known is not None and known != owner:
            raise UnsupportedConstructError(
                f"two enum definitions share the simple name {owner.name!r};"
                " rename one.",
                node=owner,
            )
        self.enum_defs[symbol] = owner
        prefix = "_sysmlc_types." if self.types_module else ""
        return f"{prefix}{symbol}.{literal.name}"

    @property
    def has_types(self) -> bool:
        """Whether any generated type (enum or dataclass) was registered."""
        return bool(self.enum_defs or self.dataclasses)

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

    def companion_module_lines(self) -> list[str]:
        """Render the ``<basename>_types.py`` module: enums + dataclasses."""
        return self.dataclasses.module_lines(
            preceding_lines=self._enum_class_lines()
        )

    def preamble_lines(self) -> list[str]:
        """Assemble the LF preamble: stdlib imports + type/function imports."""
        lines: list[str] = []
        if self.uses_logging:
            lines.append("import logging")
        if self.uses_math:
            lines.append("import math")
        for artifact in self.external:
            names = self.used_external.get(artifact)
            if not names:
                continue
            lines.extend(
                f"from {artifact.file_name} import {name}"
                for name in sorted(names)
            )
        names = sorted(self.enum_defs) + self.dataclasses.names()
        if self.types_module and names:
            lines.append(f"import {self.types_module} as _sysmlc_types")
            if self.catalog is None:
                # Catalog-free composite values use bare type references.
                # Keep these valid alongside qualified payload constructors.
                lines.extend(types_import_lines(self.types_module, names))
        return lines


def files_option(
    types_module: str | None, external: list[ForeignArtifact] | None
) -> tuple[str, str] | None:
    """Build the ``files:`` target option, or None when nothing to ship."""
    names: list[str] = []
    if types_module is not None:
        names.append(f"{types_module}.py")
    if external:
        for artifact in external:
            names.append(artifact.path.name)
    if not names:
        return None
    listed = ", ".join(f'"{n}"' for n in names)
    return ("files", f"[{listed}]")


class LfPythonCodeGen(PythonCodeGen):
    """Python code generator for Lingua Franca reaction bodies.

    Reaction bodies run as methods of the generated reactor, so attribute
    references render as ``self.<name>``. Payload bindings resolve by source
    feature identity to the delivered value. Unknown references produce
    diagnostics.

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
        local_names: Names explicitly in scope as plain locals.
            Payload bindings use source feature identity instead.
        payload_bindings: Source payload features mapped to delivered values.
        port_signals: Signals a connected peer accepts, so a ``via`` send of
            one sets the LF output port; a ``via`` send of any other signal
            is dropped and recorded in ``needs.undeliverable_sends``.
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
        payload_bindings: dict[syside.Feature, str] | None = None,
    ) -> None:
        self._needs = needs if needs is not None else PreambleNeeds()
        super().__init__(
            context,
            external=self._needs.external,
            definition_symbols={
                qn: f"_sysmlc_types.{symbol}"
                for qn, symbol in (
                    self._needs.catalog.definition_symbols.items()
                    if self._needs.catalog
                    else ()
                )
            },
        )
        self._payload_bindings = payload_bindings or {}
        self._used_external = self._needs.used_external
        self._attribute_names = attribute_names
        self._self_prefix = self_prefix
        self._local_names = local_names
        self._port_signals = port_signals

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
        if ref in self._payload_bindings:
            return self._payload_bindings[ref]
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
        """Translate a send by its declared receiver (SysML/KerML semantics).

        - A ``to`` receiver that is not the machine's own port is
          cross-machine addressing and is rejected (route with ``via`` + a
          ``connect`` instead).
        - A ``via`` send routes over the port's connections: it sets the LF
          output port when a connected peer accepts the signal, and is
          dropped (recorded for a build warning) otherwise. It never also
          schedules a self-event -- one send is one transfer.
        - A ``to <own port>`` send (or a bare send) is an internal
          self-event: ``<Event>_act.schedule(0[, <payload>])``.

        The payload constructs the type's generated class with its fields
        by keyword: first each constructor argument, named by the field it
        binds, then each omitted field with the value its type gives it, so
        the arguments are evaluated once, at the send, in constructor
        order. An omitted field without a value keeps the class's
        placeholder. A type without fields is sent as a bare signal.

        Payload constructors use the generated types module alias so LF
        port parameters cannot shadow their class symbols.

        Args:
            send: The ``send new <Type>(<args>)`` action to translate.

        Returns:
            Python source for the statement, or ``""`` for a dropped ``via``
            send.

        Raises:
            UnsupportedConstructError: If the ``to`` receiver is not the
                machine's own port, or an omitted field's value from its
                type is not a literal constant.
            ValueError: If the payload is not a ``new <Type>(...)``
                constructor resolving to a named definition, or an argument
                does not bind exactly one field.
        """
        event_name, _arguments = payload_signature(send)
        definition = constructed_payload_definition(send)
        if self._needs.catalog is not None:
            assert definition is not None
            event_name = self._needs.catalog.channel_for(definition)
        symbol = (
            self._needs.dataclasses.symbol_for(definition)
            if definition is not None
            else event_name
        )
        constructor = (
            f"_sysmlc_types.{symbol}" if self._needs.types_module else symbol
        )
        if send.receiver_argument is not None and not send_receiver_is_own_port(
            send
        ):
            raise UnsupportedConstructError(
                f"send {event_name!r} addresses a receiver that is not the "
                "machine's own port; cross-machine 'to' addressing is not "
                "supported. Route the signal with 'via <port>' and a "
                "connect instead.",
                node=send,
            )
        via_port = send_via_port(send)
        if via_port is not None and event_name not in self._port_signals:
            self._needs.undeliverable_sends.add((event_name, via_port))
            return ""
        fields = payload_fields(send)
        args = self.render_fields((field.name, field.value) for field in fields)
        payload = f"{constructor}({args})" if fields else None
        if via_port is not None:
            return f"{event_name}.set({payload or 'True'})"
        return (
            f"{event_name}_act.schedule(0, {payload})"
            if payload
            else f"{event_name}_act.schedule(0)"
        )

    @override
    def _emit_invocation(self, expr: syside.InvocationExpression) -> str:
        """Emit a shared library call or backend-specific calc-def call."""
        library_call = self._emit_library_invocation(expr)
        if library_call is not None:
            source, needs_math = library_call
            if needs_math:
                self._needs.uses_math = True
            return source
        external_call = self._emit_external_calculation_invocation(
            expr,
            external=self._needs.external,
            used_external=self._needs.used_external,
        )
        if external_call is not None:
            return external_call
        func = expr.function
        qn = None if func is None else func.qualified_name
        raise UnsupportedConstructError(
            f"function {qn or '<unresolved>'!s} is not in rosetta's "
            "supported set.",
            node=expr,
        )

    def _require_attribute(self, name: str, node: syside.Element) -> None:
        if name not in self._attribute_names:
            raise UnsupportedConstructError(
                f"reference to {name!r} does not resolve to a bound "
                "attribute; referencing an accept payload or any other "
                "non-attribute feature is not supported by rosetta yet.",
                node=node,
            )
