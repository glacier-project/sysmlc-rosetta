"""C code generators for Lingua Franca reaction bodies (reactor-c, reactor-uc)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, override

import syside
from sysmlc.codegen.c import LIBRARY_FUNCTIONS, CCodeGen
from sysmlc.errors import UnsupportedConstructError

from sysmlc_rosetta.c_types import type_name
from sysmlc_rosetta.codegen import LfActions, PreambleNeeds
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.uc import MATH_HELPERS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sysmlc_rosetta.c_types import CTypes


class LfCCodeGen(LfActions, CCodeGen):
    """C code generator for reaction bodies of LF's C target.

    Attribute references render as ``self-><name>``. Payloads are compound
    literals of the payload's struct and enumeration literals are the
    constants of :class:`~sysmlc_rosetta.c_types.CTypes`. The arguments
    mirror :class:`~sysmlc_rosetta.codegen.LfPythonCodeGen`'s.

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


class LfUcCodeGen(LfCCodeGen):
    """C code generator for reaction bodies of the uC target.

    uC's native build links no math library: ``abs``, ``max`` and
    ``min`` call the target's :data:`~sysmlc_rosetta.targets.uc.MATH_HELPERS`,
    and the other library functions are rejected.
    """

    _library_functions: ClassVar[Mapping[str, str]] = {
        function: callee for function, (callee, _) in MATH_HELPERS.items()
    }

    @override
    def _emit_invocation(self, expr: syside.InvocationExpression) -> str:
        """Emit a call, rejecting the library functions of ``<math.h>``.

        Raises:
            UnsupportedConstructError: If the call needs the math library.
        """
        function = expr.function
        name = None if function is None else str(function.qualified_name)
        if name in LIBRARY_FUNCTIONS and name not in self._library_functions:
            raise UnsupportedConstructError(
                f"{name} needs the C math library, which reactor-uc's native "
                "build does not link"
            )
        return super()._emit_invocation(expr)
