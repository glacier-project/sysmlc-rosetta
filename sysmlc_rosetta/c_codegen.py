"""C code generators for Lingua Franca reaction bodies (reactor-c, reactor-uc)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, override

import syside
from sysmlc.codegen.c import CCodeGen
from sysmlc.errors import UnsupportedConstructError

from sysmlc_rosetta.c_types import type_name
from sysmlc_rosetta.codegen import LfActions, PreambleNeeds
from sysmlc_rosetta.fixed_width import FIXED_WIDTH
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.width import WIDE_COMPARISON, negated_literal

if TYPE_CHECKING:
    from sysmlc_rosetta.c_types import CTypes

# C gives a bare decimal literal a signed type when this holds it.
_INT64: Final = FIXED_WIDTH["int64"]


class LfCCodeGen(LfActions, CCodeGen):
    """C code generator for reaction bodies of LF's C target.

    Attribute references render as ``self-><name>``. Payloads are compound
    literals of the payload's struct and enumeration literals are the
    constants of :class:`~sysmlc_rosetta.c_types.CTypes`. The arguments
    mirror :class:`~sysmlc_rosetta.codegen.LfPythonCodeGen`'s.

    Fixed-width arithmetic is spelled from the width evaluation of the
    expression being rendered (:class:`~sysmlc_rosetta.width.Evaluation`):
    store casts on its cast leaves, ``int64_t`` operands for its widened
    comparisons, integer ``abs``/``min``/``max`` and single-precision real
    literals. Without fixed-width features the C is unchanged.

    Args:
        attribute_names: Simple names of the machine's bound attributes.
        needs: The shared preamble registry; its target must be a
            :class:`CTarget`.
        self_prefix: When False, references render bare (initializers).
        local_names: Names in scope as plain locals.
        port_signals: Signals a connected peer accepts.
        payload_bindings: Source payload features mapped to delivered values.

    Raises:
        TypeError: If ``needs`` targets another language.
    """

    def __init__(
        self,
        attribute_names: frozenset[str],
        *,
        needs: PreambleNeeds | None = None,
        self_prefix: bool = True,
        local_names: frozenset[str] = frozenset(),
        port_signals: frozenset[str] = frozenset(),
        payload_bindings: dict[syside.Feature, str] | None = None,
    ) -> None:
        needs = needs if needs is not None else PreambleNeeds(target=CTarget())
        if not isinstance(needs.target, CTarget):
            raise TypeError("LfCCodeGen needs a C target")
        self._types: CTypes = needs.target.types
        super().__init__(external=needs.external)
        self._bind(
            needs,
            attribute_names,
            self_prefix=self_prefix,
            local_names=local_names,
            port_signals=port_signals,
            payload_bindings=payload_bindings,
        )

    @override
    def type_symbol(self, definition: syside.Definition) -> str:
        """Register a structured definition and return its C type."""
        self._types.register(definition)
        return type_name(definition)

    @override
    def _enum_literal(self, literal: syside.EnumerationUsage) -> str:
        return self._types.enum_literal(literal)

    @override
    def _emit(self, expr: syside.Expression, parent_precedence: int = 0) -> str:
        """Emit a node; a cast leaf as ``(<its kind>)(<leaf>)``."""
        source = super()._emit(expr, parent_precedence)
        kind = self._evaluation.kind(expr)
        if kind is not None and expr in self._evaluation.cast_leaves:
            return f"({kind.ctype})({source})"
        return source

    @override
    def _emit_binary(
        self, expr: syside.OperatorExpression, parent_precedence: int
    ) -> str:
        """Emit a binary expression; a widened comparison casts both sides."""
        if expr not in self._evaluation.widened:
            return super()._emit_binary(expr, parent_precedence)
        token, precedence = self._binary_operators[expr.operator]
        lhs, rhs = (
            f"({WIDE_COMPARISON})({self._emit(operand, 0)})"
            for operand in expr.operands.collect()
        )
        body = f"{lhs} {token} {rhs}"
        return f"({body})" if precedence < parent_precedence else body

    @override
    def _emit_unary(
        self, expr: syside.OperatorExpression, parent_precedence: int
    ) -> str:
        """Emit a unary expression; ``-9223372036854775808`` as ``INT64_MIN``.

        Negating ``UINT64_C(9223372036854775808)`` would wrap in
        ``uint64_t``.

        Raises:
            UnsupportedConstructError: If a negated literal is below
                ``INT64_MIN``: no C integer type holds it.
        """
        literal = negated_literal(expr)
        if literal is None or _INT64.holds(literal.value):
            return super()._emit_unary(expr, parent_precedence)
        if not _INT64.holds(-literal.value):
            raise UnsupportedConstructError(
                f"integer literal -{literal.value} does not fit in 64 bits; "
                "no C integer type holds it",
                node=literal,
            )
        return "INT64_MIN"

    @override
    def _emit_literal_integer(self, expr: syside.LiteralInteger) -> str:
        """Emit an integer literal; above ``INT64_MAX`` as ``UINT64_C``."""
        source = super()._emit_literal_integer(expr)
        return source if _INT64.holds(expr.value) else f"UINT64_C({source})"

    @override
    def _emit_literal_rational(self, expr: syside.LiteralRational) -> str:
        """Emit a real literal, with ``f`` when it is single precision."""
        source = super()._emit_literal_rational(expr)
        return f"{source}f" if expr in self._evaluation.single else source

    @override
    def _emit_library_invocation(
        self, expr: syside.InvocationExpression
    ) -> tuple[str, bool] | None:
        """Emit a library call; integer ``abs``/``min``/``max`` call helpers.

        The helpers, ``static inline`` functions of the types header,
        compute in the call's integer kind and evaluate each argument once.
        """
        if expr not in self._evaluation.integer_calls:
            return super()._emit_library_invocation(expr)
        kind = self._evaluation.kind(expr)
        function = expr.function
        assert kind is not None and function is not None
        assert function.name is not None
        name = self._types.integer_function(function.name, kind)
        arguments = ", ".join(
            self._emit(argument, 0) for argument in expr.arguments.collect()
        )
        return f"{name}({arguments})", False

    @override
    def _construct(
        self,
        definition: syside.Definition | None,
        event_name: str,
        fields: str,
    ) -> str:
        """Construct the payload as a compound literal of its C struct."""
        ctype = self._types.signal_type(event_name)
        if ctype is None and definition is not None:
            ctype = self.type_symbol(definition)
        return f"({ctype}){{ {fields} }}"

    @override
    def _emit_invocation(self, expr: syside.InvocationExpression) -> str:
        """Emit a call; an external calc def gets a C prototype.

        A calc def a C artifact backs keeps its plain name; without any C
        artifact, it is called through a declared ``extern`` function the
        user's own C file defines, as in statix (a missing definition is a
        linker error).
        """
        function = expr.function
        if not isinstance(function, syside.CalculationDefinition) or (
            self._emit_library_invocation(expr) is not None
        ):
            return super()._emit_invocation(expr)
        if self._needs.external:
            call = super()._emit_invocation(expr)
            self._types.declare_function(function, backed=True)
            return call
        name = self._types.declare_function(function, backed=False)
        arguments = ", ".join(
            self._emit(argument, 0) for argument in expr.arguments.collect()
        )
        return f"{name}({arguments})"
