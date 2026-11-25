"""Stable identifiers for Rosetta's generated state symbols."""

from __future__ import annotations

import keyword
import re

from sysmlc.codegen.names import NameRegistry

from sysmlc_rosetta.c_types import is_reserved

_LF_KEYWORDS = frozenset(
    {
        "NONE", "STAA", "STP", "action", "after", "as", "at", "const",
        "deadline", "extends", "federated", "forever", "from", "history",
        "import", "initial", "input", "interleaved", "logical", "main",
        "method", "mode", "mutable", "mutation", "never", "new", "output",
        "physical", "preamble", "private", "public", "reaction", "reactor",
        "realtime", "reset", "serializer", "shutdown", "startup", "state",
        "tardy", "target", "time", "timer", "watchdog", "widthof",
        "true", "True", "false", "False", "_",
    }
)  # fmt: skip


def identifier(name: str) -> str:
    """Return a portable base spelling for a source name."""
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name) or "state"
    if name == "_":
        name = "state"
    if name[0].isdigit() or name.startswith("__") or re.match(r"_[A-Z]", name):
        name = "state_" + name.lstrip("_")
    if is_reserved(name) and is_reserved(name + "_01"):
        name = "state_" + name
    return name


def safe(name: str) -> bool:
    """Whether a name can remain unchanged across LF, Python and C."""
    return (
        identifier(name) == name
        and name not in _LF_KEYWORDS
        and not keyword.iskeyword(name)
        and not is_reserved(name)
    )


def _candidate(preferred: str, attempt: int) -> str:
    base = identifier(preferred)
    if attempt == 0 and safe(base):
        return base
    if is_reserved(base + "_01"):
        base = "state_" + base
    return f"{base}_{max(1, attempt):02d}"


class ScopedNames:
    """Group generated identifiers by LF scope and collision namespace."""

    def __init__(self) -> None:
        self._registries: dict[tuple[str, str], NameRegistry] = {}

    def _registry(self, scope: str, namespace: str) -> NameRegistry:
        key = (scope, namespace)
        if key not in self._registries:
            self._registries[key] = NameRegistry(
                _candidate,
                key=str.casefold if namespace == "reactor" else str,
            )
        return self._registries[key]

    def reserve(self, scope: str, namespace: str, *names: str) -> None:
        """Reserve fixed LF names before assigning generated symbols.

        Raises:
            UnsupportedConstructError: A generated symbol already owns a name.
        """
        self._registry(scope, namespace).reserve(*names)

    def available(self, scope: str, namespace: str, name: str) -> bool:
        """Whether a preferred spelling can be preserved in its namespace."""
        return self._registry(scope, namespace).available(name)

    def assign(
        self, scope: str, namespace: str, identity: str, preferred: str
    ) -> str:
        """Return the stable LF identifier assigned to a generated symbol."""
        return self._registry(scope, namespace).assign(identity, preferred)
