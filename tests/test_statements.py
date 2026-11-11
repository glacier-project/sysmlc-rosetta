"""The statement tree and its Python rendering."""

from __future__ import annotations

import pytest

from sysmlc_rosetta.lifecycle import make_reaction
from sysmlc_rosetta.statements import (
    If,
    Pass,
    Stmt,
    assigns,
    insert_after_assignments,
    iter_statements,
    nest,
    writes,
)
from sysmlc_rosetta.targets.python import PythonTarget

_PY = PythonTarget()


def _assign(name: str) -> Stmt:
    return Stmt(f"self.{name} = 1", assigns=frozenset((name,)))


def test_statement_must_be_one_line() -> None:
    with pytest.raises(ValueError, match="several lines"):
        Stmt("a = 1\nb = 2")


def test_conditional_needs_a_branch() -> None:
    with pytest.raises(ValueError, match="at least one branch"):
        If(())


def test_walk_visits_nested_statements_in_order() -> None:
    body = (
        Stmt("a"),
        If((("c1", (Stmt("b"),)), ("c2", (Stmt("c"),))), orelse=(Stmt("d"),)),
        "verbatim",
        Pass(),
    )

    assert [stmt.text for stmt in iter_statements(body)] == ["a", "b", "c", "d"]


def test_writes_and_assigns_collect_nested_metadata() -> None:
    body = (
        nest("c", (Stmt("p.set(1)", writes=frozenset(("p",))), _assign("x"))),
        Stmt("q.set(1)", writes=frozenset(("q",))),
    )

    assert writes(body) == {"p", "q"}
    assert assigns(body) == {"x"}


def test_insertion_lands_in_the_assigning_branch() -> None:
    notify = Stmt("notify()")
    body = (
        If((("c", (_assign("x"), Stmt("other"))),), orelse=(_assign("y"),)),
        _assign("z"),
    )

    rewritten, changed = insert_after_assignments(
        body, frozenset(("x", "z")), notify
    )

    assert changed
    assert _PY.render(rewritten) == (
        "if c:",
        "    self.x = 1",
        "    notify()",
        "    other",
        "else:",
        "    self.y = 1",
        "self.z = 1",
        "notify()",
    )


def test_insertion_without_assignment_reports_no_change() -> None:
    body = (_assign("y"),)

    rewritten, changed = insert_after_assignments(
        body, frozenset(("x",)), Stmt("notify()")
    )

    assert not changed
    assert rewritten == body


def test_python_renders_first_match_chain() -> None:
    body = (
        If(
            (("a", (Stmt("one"),)), ("b", (nest("c", (Stmt("two"),)),))),
            orelse=(),
        ),
    )

    assert _PY.render(body) == (
        "if a:",
        "    one",
        "elif b:",
        "    if c:",
        "        two",
        "else:",
        "    pass",
    )


def test_python_fills_an_empty_branch_with_pass() -> None:
    assert _PY.render((nest("a", ()),)) == ("if a:", "    pass")


def test_effects_come_from_statement_metadata() -> None:
    # A signal named as a suffix of another (Tick / RetryTick) no longer
    # gains a spurious effect: a substring search over rendered text found
    # "Tick_act.schedule" inside "RetryTick_act.schedule".
    body = (_PY.schedule("RetryTick_act"),)

    reaction = make_reaction(("t",), (), body, ["Tick", "RetryTick"])

    assert reaction.effects == ("RetryTick_act",)
