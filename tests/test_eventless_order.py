from pathlib import Path

import pytest
import syside
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.serialize import to_lf
from tests.test_lf_harness import run_machine

FIXTURES = Path(__file__).parent / "fixtures" / "eventless-order"

# Same-source eventless alternatives declared in different lexical scopes:
# the first eligible original declaration wins and false guards are skipped.
CASES = [
    ("NestedFirst", "outer.b"),
    ("ParentFirst", "outer.c"),
    ("NestedFalseFirst", "outer.b"),
]


@pytest.fixture(scope="module")
def order_model() -> syside.Model:
    return load_model(FIXTURES)


def _mode_a(order_model: syside.Model, name: str) -> str:
    lf = to_lf(build_program(order_model, f"EventlessOrder::{name}"))
    start = lf.index("mode a {")
    return lf[start : lf.index("mode b {", start)]


def test_nested_first_dispatches_inner_declaration(
    order_model: syside.Model,
) -> None:
    mode = _mode_a(order_model, "NestedFirst")
    assert "reset(b)" in mode
    assert "b.set()" in mode
    assert "c.set()" not in mode


def test_parent_first_dispatches_root_declaration(
    order_model: syside.Model,
) -> None:
    mode = _mode_a(order_model, "ParentFirst")
    assert "reset(c)" in mode
    assert "c.set()" in mode
    assert "b.set()" not in mode


def test_false_guard_first_falls_through_in_declaration_order(
    order_model: syside.Model,
) -> None:
    mode = _mode_a(order_model, "NestedFalseFirst")
    lines = [line.strip() for line in mode.splitlines()]
    branches = lines[lines.index("if False:") :]
    assert branches[:6] == [
        "if False:",
        "c.set()",
        "elif True:",
        "b.set()",
        "else:",
        "c.set()",
    ]


@pytest.mark.lf
@pytest.mark.parametrize(("name", "expected"), CASES)
def test_eventless_declaration_order_executes(
    tmp_path: Path, name: str, expected: str
) -> None:
    states = run_machine(tmp_path, FIXTURES, f"EventlessOrder::{name}")
    assert states[-1] == expected
