"""C types of a generated program and its ``<base>_types.h`` header.

Every payload, structured attribute and enumeration a program uses gets a
C type named after its definition's qualified name, in statix's style:
lowercase snake case plus ``_t`` (``Microwave::StartCmd`` becomes
``microwave_start_cmd_t``). The names are stable, so hand-written C code
(external functions) can use them; the header is that code's contract.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final

import syside
from sysmlc.codegen.structured import named_fields
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.queries import structured_fields

if TYPE_CHECKING:
    from collections.abc import Callable

    from sysmlc.semantics.statemachine.facts import AttributeBinding

_SCALAR_C: Final[dict[str, str]] = {
    "Boolean": "bool",
    "Integer": "int32_t",
    "Natural": "int32_t",
    "Positive": "int32_t",
    "Rational": "double",
    "Real": "double",
    "Number": "double",
    "String": "const char*",
}

# C keywords, reactor-c's names (its macros, types and ``self`` members)
# and the C library functions generated code calls: a model name equal to
# one of them cannot name anything in the generated program.
C_RESERVED: Final = frozenset(
    {
        "auto", "bool", "break", "case", "char", "const", "continue",
        "default", "do", "double", "else", "enum", "extern", "false",
        "float", "for", "goto", "if", "inline", "int", "long", "register",
        "restrict", "return", "short", "signed", "sizeof", "static",
        "struct", "switch", "true", "typedef", "union", "unsigned", "void",
        "volatile", "while", "NULL", "self", "base", "env", "string",
        "tag_t", "instant_t", "interval_t", "NEVER", "FOREVER", "NEVER_TAG",
        "FOREVER_TAG", "NSEC", "NSECS", "USEC", "USECS", "MSEC", "MSECS",
        "SEC", "SECS", "MINUTE", "MINUTES", "HOUR", "HOURS", "DAY", "DAYS",
        "WEEK", "WEEKS", "printf", "fprintf", "stderr", "exit",
    }
)  # fmt: skip
# Prefixes of reactor-c's and rosetta's own generated names.
_RESERVED_PREFIXES: Final = ("lf_", "_lf", "LF_", "sysmlc_", "SYSMLC_")
_SCALAR_ZERO: Final = frozenset({"bool", "int32_t", "double"})


def is_reserved(name: str) -> bool:
    """Whether ``name`` cannot name a model element in generated C."""
    return name in C_RESERVED or name.startswith(_RESERVED_PREFIXES)


def c_identifier(name: str) -> str:
    """Return ``name`` as a lowercase snake_case C identifier.

    CamelCase word boundaries become underscores, as in statix, so
    ``MachineExit`` reads ``machine_exit``.
    """
    ident = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    ident = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", ident)
    ident = re.sub(r"[^0-9a-zA-Z_]", "_", ident)
    return re.sub(r"_+", "_", ident).strip("_").lower()


def qualified_identifier(element: syside.Element) -> str:
    """An element's qualified name as one C identifier."""
    qualified = str(element.qualified_name or element.name)
    return c_identifier(qualified.replace("::", "_"))


def type_name(definition: syside.Definition) -> str:
    """The C type name of a definition: its qualified name plus ``_t``."""
    return f"{qualified_identifier(definition)}_t"


class CTypes:
    """Collects the C types a program uses and renders their header.

    Structured definitions become ``typedef struct``s, enumerations
    ``typedef enum``s with a name table (``<type>_names``) for printing.
    A payload type without fields carries no data: its signal is a plain
    ``bool`` port.
    """

    def __init__(self) -> None:
        self._structs: dict[str, tuple[str, ...]] = {}
        self._fields: dict[str, dict[str, str]] = {}
        self._prototypes: dict[str, str] = {}
        self._enums: dict[str, tuple[str, ...]] = {}
        self._signals: dict[str, str | None] = {}
        self._in_progress: set[str] = set()

    # -- registration --

    def register(self, definition: syside.Definition) -> str | None:
        """Register a structured definition; return its C type or None.

        A definition without fields has no C type: None.

        Raises:
            UnsupportedConstructError: If a field's type has no C mapping or
                the definition is recursive.
        """
        fields = named_fields(definition)
        if not fields:
            return None
        name = type_name(definition)
        if name in self._structs:
            return name
        if name in self._in_progress:
            raise UnsupportedConstructError(
                f"structured type {definition.qualified_name!s} is recursive",
                node=definition,
            )
        self._in_progress.add(name)
        try:
            types = {
                _member(field_name, field): self.field_type(field)
                for field_name, field in fields
            }
        finally:
            self._in_progress.discard(name)
        self._fields[name] = types
        members = tuple(f"  {ctype} {field};" for field, ctype in types.items())
        self._structs[name] = (
            "typedef struct {",
            *members,
            f"}} {name};",
        )
        return name

    def register_enum(self, definition: syside.EnumerationDefinition) -> str:
        """Register an enumeration; return its C type."""
        name = type_name(definition)
        if name not in self._enums:
            literals = tuple(
                literal.name
                for literal in definition.owned_members.collect()
                if isinstance(literal, syside.EnumerationUsage)
                and literal.name is not None
            )
            self._enums[name] = literals
        return name

    def field_literal(self, struct: str, field: str, value: object) -> str:
        """A C literal for a JSON scalar ``value`` of a struct's field.

        A string becomes the constant of the field's enumeration when the
        field is one, else a string literal.
        """
        ctype = self._fields[struct][field]
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            if ctype in self._enums:
                return _enum_constant(ctype, value)
            return json.dumps(value)
        return repr(value)

    def enum_literal(self, literal: syside.EnumerationUsage) -> str:
        """The C constant of an enumeration literal.

        Raises:
            UnsupportedConstructError: If the literal is not owned by an
                enumeration definition.
        """
        owner = literal.owner
        if not isinstance(owner, syside.EnumerationDefinition):
            raise UnsupportedConstructError(
                "enum literal is not owned by an enumeration definition",
                node=literal,
            )
        return _enum_constant(self.register_enum(owner), str(literal.name))

    def set_signal(
        self, channel: str, definition: syside.Definition | None
    ) -> None:
        """Record the payload type a signal channel carries."""
        self._signals[channel] = (
            None if definition is None else self.register(definition)
        )

    # -- lookup --

    def signal_type(self, channel: str) -> str | None:
        """The payload C type of a channel, or None for a bare signal."""
        return self._signals.get(channel)

    def payload_signals(self) -> list[tuple[str, str]]:
        """Every channel that carries a payload, with its C type, sorted."""
        return sorted(
            (channel, ctype)
            for channel, ctype in self._signals.items()
            if ctype is not None
        )

    def field_type(self, feature: syside.Feature) -> str:
        """The C type of a field, attribute or function parameter.

        Its first type with a C mapping wins: the declared type comes before
        the library supertypes it specializes.

        Raises:
            UnsupportedConstructError: If no type has a C mapping.
        """
        for definition in feature.types.collect():
            ctype = self.definition_type(definition)
            if ctype is not None:
                return ctype
        raise UnsupportedConstructError(
            f"{feature.qualified_name!s} has no C type; rosetta's C target "
            "supports Boolean, Integer, Real, String, scalar quantities, "
            "enumerations and structured attribute definitions",
            node=feature,
        )

    def declare_function(
        self, function: syside.CalculationDefinition, *, backed: bool
    ) -> str:
        """Declare an external calc def's C prototype; return its C name.

        A function an artifact backs keeps its plain name; an unbacked one
        is named after its qualified name, so it cannot collide with
        another calc def, and the user's own C file implements it. The
        prototype makes a call type-checked even when no artifact header
        declares it.

        Args:
            function: The calc def.
            backed: Whether a C artifact implements it.
        """
        name = str(function.name) if backed else qualified_identifier(function)
        if name not in self._prototypes:
            parameters = ", ".join(
                f"{self.field_type(parameter)} {parameter.name}"
                for parameter in function.inputs.collect()
            )
            result = (
                "void"
                if function.result is None
                else self.field_type(function.result)
            )
            self._prototypes[name] = (
                f"extern {result} {name}({parameters or 'void'});"
            )
        return name

    def definition_type(self, definition: syside.Type) -> str | None:
        """The C type of a type, or None when it has no C mapping."""
        if definition.name in _SCALAR_C and definition.matches_qualified_name(
            ("ScalarValues", definition.name)
        ):
            return _SCALAR_C[definition.name]
        if isinstance(definition, syside.AttributeDefinition) and (
            definition.specializes(("Quantities", "ScalarQuantityValue"))
        ):
            return "double"
        if isinstance(definition, syside.EnumerationDefinition):
            return self.register_enum(definition)
        if isinstance(definition, syside.Definition) and structured_fields(
            definition
        ):
            return self.register(definition)
        return None

    def attribute_type(self, binding: AttributeBinding) -> str:
        """The C type of a bound attribute.

        Raises:
            UnsupportedConstructError: If the attribute's type has no C
                mapping.
        """
        definition = binding.type_definition
        ctype = None if definition is None else self.definition_type(definition)
        if ctype is None and binding.type_name in _SCALAR_C:
            ctype = _SCALAR_C[binding.type_name]
        if ctype is None:
            raise UnsupportedConstructError(
                f"attribute {binding.name!r} has no C type; rosetta's C target "
                "supports Boolean, Integer, Real, String, enumerations and "
                "structured attribute definitions"
            )
        return ctype

    def zero(self, ctype: str) -> str:
        """The initializer of a value of ``ctype`` given no value."""
        if ctype in _SCALAR_ZERO:
            return "0"
        if ctype == _SCALAR_C["String"]:
            return '""'
        return f"({ctype}){{ 0 }}"  # a struct or an enumeration

    def print_format(self, ctype: str) -> tuple[str, Callable[[str], str]]:
        """A ``printf`` conversion and argument for printing as JSON."""
        if ctype == "int32_t":
            return "%ld", lambda value: f"(long)({value})"
        if ctype == "double":
            return "%.17g", lambda value: value
        if ctype == "bool":
            return "%s", lambda value: f'(({value}) ? "true" : "false")'
        if ctype == _SCALAR_C["String"]:
            return '\\"%s\\"', lambda value: value
        if ctype in self._enums:
            return '\\"%s\\"', lambda value: f"{_names(ctype)}[{value}]"
        raise UnsupportedConstructError(f"cannot print a value of type {ctype}")

    # -- rendering --

    def header_lines(self, guard: str) -> list[str]:
        """Render the types header, include guard ``guard``."""
        lines = [
            f"#ifndef {guard}",
            f"#define {guard}",
            "",
            "#include <stdbool.h>",
            "#include <stdint.h>",
            "",
        ]
        for name, literals in sorted(self._enums.items()):
            constants = ", ".join(
                _enum_constant(name, literal) for literal in literals
            )
            names = ", ".join(f'"{literal}"' for literal in literals)
            lines += [
                f"typedef enum {{ {constants} }} {name};",
                f"static const char *const {_names(name)}[] = {{ {names} }};",
                "",
            ]
        for block in self._structs.values():
            lines += [*block, ""]
        members = "".join(
            f" {ctype} {channel};" for channel, ctype in self.payload_signals()
        )
        lines += [
            f"typedef union {{ char none;{members} }} sysmlc_payload_t;",
            "",
            *self._prototypes.values(),
            *([""] if self._prototypes else []),
            f"#endif /* {guard} */",
        ]
        return lines


def _enum_constant(enum_type: str, literal: str) -> str:
    """The C constant of an enumeration literal: ``<TYPE>_<LITERAL>``."""
    return f"{enum_type.removesuffix('_t').upper()}_{c_identifier(literal).upper()}"


def _names(enum_type: str) -> str:
    """The name table of an enumeration type: ``<type>_names``."""
    return f"{enum_type.removesuffix('_t')}_names"


def _member(name: str, field: syside.AttributeUsage) -> str:
    if is_reserved(name):
        raise UnsupportedConstructError(
            f"field name {name!r} is reserved in C; rename it", node=field
        )
    return name
