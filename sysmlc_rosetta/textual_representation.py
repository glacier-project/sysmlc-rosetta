"""Support for SysML ``TextualRepresentation`` as an external Python module."""

from __future__ import annotations

import ast
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import syside

from sysmlc.errors import UnsupportedConstructError

logger = logging.getLogger(__name__)
_PYTHON_TAG = "python"


def _require_package_or_calc_def(element: syside.Element) -> None:
    """Reject a Python rep attached to anything but a package or calc def.

    A package rep carries module scaffolding (imports, constants, private
    helpers); a calc-def rep carries that function's body. A rep attached
    to any other element (an action, a state, ...) has no place in the
    generated module: its text would land at module top level and execute
    at import time.
    """
    if isinstance(element, syside.Package | syside.CalculationDefinition):
        return
    raise UnsupportedConstructError(
        "a Python textual representation is attached to an element that "
        "is neither a package nor a calc def; rosetta collects Python "
        "bodies from packages (module scaffolding) and calc defs "
        "(function bodies) only.",
        node=element,
    )


def _collect_lines(element: syside.Element) -> list[str]:
    code: list[str] = []

    for tr in element.textual_representations.collect():
        if tr.language.strip().lower() != _PYTHON_TAG:
            continue
        _require_package_or_calc_def(element)
        if code:
            code.extend(("", ""))
        code.extend(tr.body.splitlines())

    for child in element.owned_elements.collect():
        child_lines = _collect_lines(child)
        if not child_lines:
            continue
        if code:
            code.extend(("", ""))
        code.extend(child_lines)

    return code


def _collect_code(model: syside.Model) -> list[str]:
    code: list[str] = []
    for element in model.elements(
        syside.Element,
        include_subtypes=True,
        considered_document_kinds=syside.DocumentKind.MODEL,
    ):
        if getattr(element, "owner", None) is not None:
            continue
        lines = _collect_lines(element)
        if not lines:
            continue
        if code:
            code.extend(("", ""))
        code.extend(lines)
    return code


def _top_level_names(src: str) -> frozenset[str]:
    """Return all top-level function and class names from Python source."""
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        logger.warning(
            "TextualRepresentation body is not valid Python (%s); "
            "name extraction skipped — the generated preamble import may "
            "be incomplete.",
            exc,
        )
        return frozenset()
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        )
        and isinstance(getattr(node, "col_offset", 1), int)
        and node.col_offset == 0
    )


def extract_textual(
    model: syside.Model,
    scope_qn: str,
    *,
    module_name: str | None = None,
) -> tuple[str, frozenset[str], tuple[str, ...]] | None:
    """Extract Python source from TextualRepresentation annotations."""
    lines = _collect_code(model)
    if not lines:
        return None

    src_code = "\n".join(lines)
    if not src_code.strip():
        raise UnsupportedConstructError(
            "Python TextualRepresentation bodies found in the model while "
            f"processing {scope_qn!r} but all are empty."
        )
    stem = module_name or f"{scope_qn.split('::')[-1]}_impl"
    names = _top_level_names(src_code)

    header = [
        "# Auto-generated from SysML TextualRepresentation bodies.",
        "# Do not edit — regenerate from the SysML source instead.",
        "",
    ]
    full_lines = tuple(header) + tuple(lines)

    return stem, names, full_lines


def write_module(
    src_lines: list[str] | tuple[str, ...],
    out_dir: Path,
    module_name: str,
) -> Path:
    """Write the Python file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{module_name}.py"
    path.write_text("\n".join(src_lines) + "\n")
    return path
