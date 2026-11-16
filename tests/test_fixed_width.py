"""Fixed-width width table and feature lookup."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import astuple

import pytest
import syside
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.fixed_width import FIXED_WIDTH, FixedWidth, fixed_width_of
from tests.conftest import FIXTURES_DIR

LIBRARY = "FixedWidthScalarValues"


@contextmanager
def _features() -> Iterator[dict[str, syside.Feature]]:
    """Yield every feature of the fixture and library by qualified name."""
    model = load_model(FIXTURES_DIR / "fixed-width")
    found: dict[str, syside.Feature] = {}

    def walk(node: syside.Namespace) -> None:
        for member in node.owned_members.collect():
            if isinstance(member, syside.Feature) and member.qualified_name:
                found[str(member.qualified_name)] = member
            if isinstance(member, syside.Namespace):
                walk(member)

    stack = []
    try:
        for document in model.documents:
            lock = document.lock()
            root = lock.__enter__()
            stack.append(lock)
            walk(root.root_node)
        yield found
    finally:
        for lock in reversed(stack):
            lock.__exit__(None, None, None)


def _integer_bound(expr: object) -> int:
    if isinstance(expr, syside.LiteralInteger):
        return expr.value
    assert isinstance(expr, syside.OperatorExpression)
    assert expr.operator == syside.Operator.Minus
    (operand,) = expr.operands.collect()
    return -_integer_bound(operand)


def _bounds(expr: object, out: dict[str, int]) -> None:
    assert isinstance(expr, syside.OperatorExpression)
    operands = expr.operands.collect()
    if expr.operator == syside.Operator.LogicalAnd:
        for operand in operands:
            _bounds(operand, out)
        return
    out[str(expr.operator)] = _integer_bound(operands[1])


def _library_range(feature: syside.Feature) -> tuple[int, int] | None:
    invariants = [
        m
        for m in feature.owned_members.collect()
        if isinstance(m, syside.Invariant)
    ]
    if not invariants:
        return None
    bounds: dict[str, int] = {}
    _bounds(invariants[0].result_expression, bounds)
    lower = bounds.get(str(syside.Operator.GreaterEqual), 0)
    return (lower, bounds[str(syside.Operator.LessEqual)])


def _width(qn: str) -> FixedWidth | None:
    with _features() as features:
        return fixed_width_of(features[qn])


def test_table_matches_library() -> None:
    with _features() as features:
        library = {
            qn: f
            for qn, f in features.items()
            if qn.startswith(f"{LIBRARY}::") and qn.count("::") == 1
        }
        assert {f"{LIBRARY}::{name}" for name in FIXED_WIDTH} == set(library)
        for name, entry in FIXED_WIDTH.items():
            expected = _library_range(library[f"{LIBRARY}::{name}"])
            actual = None if entry.range is None else astuple(entry.range)
            assert actual == expected, name
            assert name == entry.name


def test_table_properties() -> None:
    assert FIXED_WIDTH["uint8"].ctype == "uint8_t"
    assert FIXED_WIDTH["float32"].ctype == "float"
    assert FIXED_WIDTH["float64"].ctype == "double"
    assert FIXED_WIDTH["float32"].is_float and not FIXED_WIDTH["int8"].is_float
    assert FIXED_WIDTH["int8"].is_signed and not FIXED_WIDTH["uint8"].is_signed
    assert not FIXED_WIDTH["float64"].is_signed
    assert FIXED_WIDTH["int16"].bits == 16 and FIXED_WIDTH["float32"].bits == 32
    wide = {n for n, e in FIXED_WIDTH.items() if e.rank_at_least_int}
    assert wide == {"int32", "uint32", "int64", "uint64"}


def _ctype(qn: str) -> str | None:
    width = _width(qn)
    return None if width is None else width.ctype


def test_width_of_direct_subset() -> None:
    assert _width("FixedWidth::Machine::count") == FIXED_WIDTH["uint8"]
    assert _ctype("FixedWidth::Machine::z") == "int64_t"
    assert _ctype("FixedWidth::Machine::f") == "float"
    assert _ctype("FixedWidth::Reading::raw") == "uint16_t"


def test_width_through_user_refinement() -> None:
    assert _width("FixedWidth::pc") == FIXED_WIDTH["uint8"]


def test_width_through_redefinition() -> None:
    assert _width("FixedWidth::Special::count") == FIXED_WIDTH["uint8"]


def test_plain_types_have_no_width() -> None:
    assert _width("FixedWidth::Machine::r") is None
    assert _width("FixedWidth::Machine::p") is None


def test_same_name_elsewhere_is_not_matched() -> None:
    assert _width("FixedWidth::Other::fake") is None


def test_several_kinds_are_rejected() -> None:
    with pytest.raises(UnsupportedConstructError, match=r"int32, uint8"):
        _width("FixedWidth::Both::mixed")


def test_redefinition_cannot_change_the_kind() -> None:
    # Wider's count still specializes Machine's uint8 count, so its uint8
    # range applies too: two kinds, not the nearest one.
    with pytest.raises(UnsupportedConstructError, match=r"uint16, uint8"):
        _width("FixedWidth::Wider::count")


def test_library_feature_is_its_own_kind() -> None:
    assert _width(f"{LIBRARY}::int16") == FIXED_WIDTH["int16"]
