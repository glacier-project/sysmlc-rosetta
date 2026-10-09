import json
from dataclasses import asdict
from pathlib import Path

import pytest
import syside
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.parts import build_part_program

_FIXTURE = Path(__file__).parent / "fixtures" / "constraint-identity"


@pytest.fixture(scope="module")
def model() -> syside.Model:
    return load_model(_FIXTURE)


@pytest.mark.parametrize(
    "machine,names",
    [
        ("DuplicateConditions", ("firstLimit", "secondLimit")),
        ("Anonymous", (None, "constraint0")),
        ("Delayed", ('limit "quoted"',)),
    ],
)
def test_assertions_report_unambiguous_preserved_source_identities(
    model: syside.Model, machine: str, names: tuple[str | None, ...]
) -> None:
    program = build_program(model, f"ConstraintIdentity::{machine}")
    startup = next(
        r for r in program.reactor.reactions if r.triggers == ("startup",)
    )
    assert tuple(c.name for c in program.constraints) == names
    assert len({c.check_id for c in program.constraints}) == len(names)
    for assertion, constraint in zip(
        startup.body, program.constraints, strict=True
    ):
        condition, message = assertion.removeprefix("assert ").split(", ", 1)
        assert condition in {"self.level > 0.0"}
        diagnostic = json.loads(message)
        payload = json.loads(
            diagnostic.removeprefix("SysML constraint violated: ")
        )
        assert payload == asdict(constraint)
        assert constraint.reactor == machine
        assert constraint.scope == ""
        assert constraint.behavior == f"ConstraintIdentity::{machine}"


def test_part_preserves_constraint_source_when_the_reactor_is_renamed(
    model: syside.Model,
) -> None:
    program = build_part_program(model, "ConstraintIdentity::system")
    assert program.constraints
    assert all(c.reactor == "Controller" for c in program.constraints)
    assert all(
        c.behavior == "ConstraintIdentity::DuplicateConditions"
        for c in program.constraints
    )
