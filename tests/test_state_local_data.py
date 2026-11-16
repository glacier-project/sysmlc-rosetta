from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.program import Instantiation, MainReactor, Reaction
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.python import PythonTarget
from sysmlc_rosetta.targets.uc import UcTarget
from tests.conftest import FIXTURES_DIR
from tests.test_nested_data import _execute, _values

if TYPE_CHECKING:
    from pathlib import Path

    from sysmlc_rosetta.targets.base import LfTarget

FIXTURE = FIXTURES_DIR / "state-local-data"


@pytest.mark.lf
@pytest.mark.parametrize("target_type", [PythonTarget, CTarget, UcTarget])
def test_original_state_local_comparison(
    tmp_path: Path, target_type: type[LfTarget]
) -> None:
    target = target_type()
    program = build_program(
        load_model(FIXTURE), "CompositeStateLocal::Machine", target=target
    )
    ctype = next(
        v.type for v in program.reactor.state_vars if v.name == "observedValue"
    )
    observed = Reaction(
        ("shutdown",),
        (),
        target.render((target.observe_value("observedValue", ctype),)),
    )
    program = replace(
        program,
        reactors=(
            *program.reactors[:-1],
            replace(
                program.reactor,
                reactions=(*program.reactor.reactions, observed),
            ),
        ),
    )
    result = _execute(tmp_path, program, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _values(result.stdout, "observedValue") == [42]


@pytest.mark.lf
@pytest.mark.parametrize("target_type", [PythonTarget, CTarget, UcTarget])
def test_local_shadowing_owner_activation_and_independent_instances(
    tmp_path: Path, target_type: type[LfTarget]
) -> None:
    target = target_type()
    program = build_program(
        load_model(FIXTURE), "StateLocalData::Lifetime", target=target
    )
    program = replace(
        program,
        main=MainReactor(
            instantiations=(
                Instantiation("first", "Lifetime"),
                Instantiation("second", "Lifetime"),
            )
        ),
    )
    result = _execute(tmp_path, program, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(_values(result.stdout, "sequence")) == [131, 131, 262, 262]
    assert sorted(_values(result.stdout, "visits")) == [1, 1, 2, 2]
    assert _values(result.stdout, "temperature") == [100] * 4
    assert _values(result.stdout, "enabled") == [True] * 4


@pytest.mark.lf
@pytest.mark.parametrize("target_type", [PythonTarget, CTarget, UcTarget])
def test_private_regions_mutate_distinct_local_declarations(
    tmp_path: Path, target_type: type[LfTarget]
) -> None:
    target = target_type()
    program = build_program(
        load_model(FIXTURE), "StateLocalRegions::PrivateRegions", target=target
    )
    types = {v.name: v.type for v in program.reactor.state_vars}
    observation = Reaction(
        ("shutdown",),
        (),
        target.render(
            tuple(
                target.observe_value(name, types[name])
                for name in ("sequence", "visits")
            )
        ),
    )
    program = replace(
        program,
        reactors=(
            *program.reactors[:-1],
            replace(
                program.reactor,
                reactions=(*program.reactor.reactions, observation),
            ),
        ),
    )
    result = _execute(tmp_path, program, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _values(result.stdout, "sequence") == [11]
    assert _values(result.stdout, "visits") == [22]


def test_owner_local_shared_by_inner_regions_remains_diagnosed() -> None:
    with pytest.raises(
        UnsupportedConstructError, match="under a parallel state"
    ):
        build_program(
            load_model(FIXTURE), "StateLocalRegions::SharedWithinRegion"
        )


@pytest.mark.parametrize(
    ("redefinition", "selected"),
    [
        (":>> count default 2;", "count"),
        (":>> inner { :>> count default 2; }", "inner.count"),
    ],
)
def test_scoped_shorthand_field_value_redefinition_is_diagnosed(
    tmp_path: Path, redefinition: str, selected: str
) -> None:
    (tmp_path / "model.sysml").write_text(f"""
        package LocalFieldValue {{
            private import ScalarValues::*;
            attribute def Inner {{ attribute count : Integer default 1; }}
            attribute def Box {{
                attribute count : Integer default 1;
                attribute inner : Inner;
            }}
            state def Machine {{
                attribute observedValue : Integer := 0;
                entry; then enclosing;
                state enclosing {{
                    attribute box : Box {{ {redefinition} }}
                    entry; then child;
                    state child {{
                        entry assign observedValue := box.{selected};
                    }}
                }}
            }}
        }}
    """)
    with pytest.raises(
        UnsupportedConstructError,
        match="structured field redefinitions require explicit attribute",
    ):
        build_program(load_model(tmp_path), "LocalFieldValue::Machine")


@pytest.mark.parametrize("initializer", ["rootValue", "rootDelay"])
def test_activation_dependent_local_initializer_is_diagnosed(
    tmp_path: Path, initializer: str
) -> None:
    declared = "Integer" if initializer == "rootValue" else "DurationValue"
    (tmp_path / "model.sysml").write_text(f"""
        package LocalInit {{
            private import ScalarValues::*;
            private import ISQ::DurationValue;
            private import SI::*;
            state def Machine {{
                attribute rootValue : Integer := 41;
                attribute rootDelay : DurationValue := 0.001 [s];
                entry; then enclosing;
                state enclosing {{
                    attribute local : {declared} := {initializer};
                    entry; then child;
                    state child;
                }}
            }}
        }}
    """)
    with pytest.raises(
        UnsupportedConstructError,
        match="state-local initializers require literal constants",
    ):
        build_program(load_model(tmp_path), "LocalInit::Machine")
