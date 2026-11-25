from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import RosettaBuilder, build_program
from sysmlc_rosetta.program import LfProgram
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.python import PythonTarget
from sysmlc_rosetta.targets.uc import UcTarget
from tests.conftest import FIXTURES_DIR
from tests.test_nested_data import _execute, _values
from tests.test_state_local_data import _with_final_values

if TYPE_CHECKING:
    from pathlib import Path

    from sysmlc.semantics.statemachine.facts import AttributeBinding

    from sysmlc_rosetta.targets.base import LfTarget

FIXTURE = FIXTURES_DIR / "data-resolution"


def test_qualified_unbound_declaration_cannot_alias_machine_storage() -> None:
    with pytest.raises(
        UnsupportedConstructError, match="does not name a bound attribute"
    ):
        build_program(load_model(FIXTURE), "DataResolution::Qualified")


def test_local_binding_uses_existing_feature_metadata() -> None:
    class ExistingBindingBuilder(RosettaBuilder):
        def bind_attribute(self, binding: AttributeBinding) -> None:
            super().bind_attribute(replace(binding, source_feature=None))

    builder = ExistingBindingBuilder("Local")
    program = StateMachineDriver(load_model(FIXTURE)).run(
        "DataResolution::Local", builder
    )
    assert isinstance(program, LfProgram)


def test_unused_parallel_protocol_name_remains_available() -> None:
    program = build_program(load_model(FIXTURE), "DataResolution::Flat")
    assert program.reactor.modes[0].name == "_previous_current_state"


@pytest.mark.lf
@pytest.mark.parametrize("target_type", [PythonTarget, CTarget, UcTarget])
def test_nested_constraint_checks_each_intermediate_write(
    tmp_path: Path, target_type: type[LfTarget]
) -> None:
    target = target_type()
    program = build_program(
        load_model(FIXTURE), "DataResolution::Intermediate", target=target
    )
    result = _execute(tmp_path, program, target)
    assert result.returncode != 0
    assert "SysML constraint violated:" in result.stdout + result.stderr
    assert "inOrder" in result.stdout + result.stderr


@pytest.mark.lf
@pytest.mark.parametrize("target_type", [PythonTarget, CTarget, UcTarget])
def test_named_parallel_children_share_ordered_machine_data(
    tmp_path: Path, target_type: type[LfTarget]
) -> None:
    target = target_type()
    program = build_program(
        load_model(FIXTURE), "DataResolution::NamedParallel", target=target
    )
    program = _with_final_values(program, target, ("observedValue",))
    result = _execute(tmp_path, program, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _values(result.stdout, "observedValue") == [12]
