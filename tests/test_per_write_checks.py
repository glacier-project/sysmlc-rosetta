"""Asserted constraints are checked right after each write they mention."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.statements import (
    If,
    Stmt,
    insert_after_assignments,
    insert_checks_after_writes,
)
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.python import PythonTarget
from tests.conftest import FIXTURES_DIR

if TYPE_CHECKING:
    from sysmlc_rosetta.program import LfProgram, Reaction
    from sysmlc_rosetta.targets.base import LfTarget

_FIXTURE = FIXTURES_DIR / "per-write-checks"
_TARGETS = {"python": PythonTarget, "c": CTarget}


@pytest.fixture(params=sorted(_TARGETS))
def target(request: pytest.FixtureRequest) -> LfTarget:
    return _TARGETS[request.param]()


def _build(name: str, target: LfTarget) -> LfProgram:
    return build_program(
        load_model(_FIXTURE), f"PerWriteChecks::{name}", target=target
    )


def _reactions(program: LfProgram) -> list[Reaction]:
    reactor = program.reactor
    found = list(reactor.reactions) + list(reactor.trailing_reactions)
    for mode in reactor.modes:
        found.extend(mode.reactions)
    return found


def _startup(program: LfProgram) -> Reaction:
    (startup,) = [
        r for r in program.reactor.reactions if r.triggers == ("startup",)
    ]
    return startup


def _is_check(line: str) -> bool:
    return "SysML constraint violated" in line


def _body_with(program: LfProgram, marker: str) -> list[str]:
    """Return the lines of the one non-startup reaction containing marker."""
    (found,) = [
        list(r.body)
        for r in _reactions(program)
        if r.triggers != ("startup",) and any(marker in x for x in r.body)
    ]
    return found


def test_check_after_each_write(target: LfTarget) -> None:
    body = _body_with(_build("Twice", target), "count + 1")
    writes = [i for i, line in enumerate(body) if "count + 1" in line]
    checks = [i for i, line in enumerate(body) if "count <= 200" in line]
    first, second = writes
    # Each check starts on the line right after its write; the second write
    # is the one after the first check's lines.
    assert checks[0] == first + 1
    assert checks[1] == second + 1
    assert len(checks) == 2


def test_no_check_after_a_trailing_non_assignment(target: LfTarget) -> None:
    body = _body_with(_build("Twice", target), "count + 1")
    last_check = max(i for i, line in enumerate(body) if "count <= 200" in line)
    switch = next(
        i for i, line in enumerate(body) if "idle" in line and i > last_check
    )
    assert not any("count <= 200" in line for line in body[switch:])


def test_no_check_for_unmentioned_attribute(target: LfTarget) -> None:
    program = _build("Unrelated", target)
    body = _body_with(program, "other")
    assert not any("count <= 200" in line for line in body)
    assert any("count <= 200" in line for line in _startup(program).body)


def test_check_inside_if_branch(target: LfTarget) -> None:
    program = _build("Branchy", target)
    body = _body_with(program, "count + 1")
    write = next(i for i, line in enumerate(body) if "count + 1" in line)
    check = next(i for i, line in enumerate(body) if "count <= 200" in line)
    assert check == write + 1
    indent = len(body[write]) - len(body[write].lstrip())
    assert len(body[check]) - len(body[check].lstrip()) == indent
    # The else branch writes nothing, so nothing is checked outside the
    # branch.
    assert sum("count <= 200" in line for line in body) == 1


def test_constraint_with_two_attributes_checked_after_either_write(
    target: LfTarget,
) -> None:
    program = _build("Pair", target)
    body = _body_with(program, "low = 10")
    low = next(i for i, line in enumerate(body) if "low = 10" in line)
    high = next(i for i, line in enumerate(body) if "high = 20" in line)
    assert "low <= " in body[low + 1]
    assert "low <= " in body[high + 1]
    assert sum("low <= " in line for line in body) == 2


def test_statement_writing_both_attributes_gets_one_check() -> None:
    both, other = Stmt("check both"), Stmt("check other")
    body = (Stmt("w", assigns=frozenset(("low", "high"))),)
    rewritten, changed = insert_checks_after_writes(
        body,
        [(both, frozenset(("low", "high"))), (other, frozenset(("high",)))],
    )
    assert changed
    assert rewritten == (body[0], both, other)


def test_checks_recurse_into_both_branches() -> None:
    check = Stmt("check")
    body = (
        If(
            (("c", (Stmt("w", assigns=frozenset(("x",))),)),),
            orelse=(Stmt("v", assigns=frozenset(("x",))),),
        ),
    )
    rewritten, changed = insert_checks_after_writes(
        body, [(check, frozenset(("x",)))]
    )
    assert changed
    (node,) = rewritten
    assert isinstance(node, If)
    assert node.branches[0][1][1] == check
    assert node.orelse is not None
    assert node.orelse[1] == check


def test_startup_check_kept(target: LfTarget) -> None:
    program = _build("Pair", target)
    assert any("low <= " in line for line in _startup(program).body)


def test_check_precedes_change_notification(target: LfTarget) -> None:
    program = _build("Notified", target)
    body = _body_with(program, "count + 1")
    write = next(i for i, line in enumerate(body) if "count + 1" in line)
    check = next(i for i, line in enumerate(body) if "count <= 200" in line)
    notify = next(i for i, line in enumerate(body) if "_change_act" in line)
    assert write < check < notify


def test_constants_bound_by_literal_are_never_checked_on_their_own(
    target: LfTarget,
) -> None:
    # A literal binding is a read-only constant, and an `in` attribute an LF
    # parameter: neither is ever written by a statement, so a constraint is
    # re-checked only when its assignable attribute changes.
    program = _build("Bound", target)
    body = _body_with(program, "count + 1")
    assert sum("count <= " in line for line in body) == 1
    assert any("count <= " in line for line in _startup(program).body)


def test_checks_follow_calls_and_boolean_operators(target: LfTarget) -> None:
    body = _body_with(_build("Shapes", target), "= 1")
    a_write, c_write, d_write = (
        i
        for i, line in enumerate(body)
        if line.strip().rstrip(";") in {"self.a = 1", "self->a = 1"}
        or line.strip().rstrip(";") in {"self.c = 1", "self->c = 1"}
        or line.strip().rstrip(";") in {"self.d = 1", "self->d = 1"}
    )
    call = next(i for i, line in enumerate(body) if "max(" in line)
    boolean = next(i for i, line in enumerate(body) if "c >= " in line)
    # a is in both constraints (declaration order), c only in the second
    # (through `and`), d in none.
    assert a_write < call < boolean < c_write
    assert sum("c >= " in line for line in body) == 2
    assert sum("max(" in line for line in body) == 1
    assert d_write > c_write
    assert not any("max(" in line or "c >= " in line for line in body[d_write:])


def test_check_after_a_write_through_a_chained_target(
    target: LfTarget,
) -> None:
    program = _build("Fielded", target)
    body = _body_with(program, "pt.x")
    write = next(i for i, line in enumerate(body) if "pt.x =" in line)
    assert "pt.x >= 0" in body[write + 1]
    assert any("pt.x >= 0" in line for line in _startup(program).body)


def test_chained_write_is_not_an_assignment_for_notifications() -> None:
    check = Stmt("check")
    body = (Stmt("pt.x = 1", assigns_through=frozenset(("pt",))),)
    notified, changed = insert_after_assignments(
        body, frozenset(("pt",)), Stmt("notify")
    )
    assert not changed
    assert notified == body
    checked, changed = insert_checks_after_writes(
        body, [(check, frozenset(("pt",)))]
    )
    assert changed
    assert checked == (*body, check)
