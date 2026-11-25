from __future__ import annotations

import ast
from typing import TYPE_CHECKING

import pytest
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import RosettaBuilder, build_program
from sysmlc_rosetta.codegen import PreambleNeeds
from sysmlc_rosetta.parts import build_part_program, compose_exhibits
from sysmlc_rosetta.program import LfProgram
from sysmlc_rosetta.serialize import to_lf
from sysmlc_rosetta.targets import TARGETS
from tests.conftest import FIXTURES_DIR

if TYPE_CHECKING:
    import syside


@pytest.fixture(scope="module")
def named_states_model() -> syside.Model:
    return load_model(FIXTURES_DIR / "state-names")


@pytest.mark.parametrize("target", ["python", "c", "uc"])
def test_dotted_modes_timers_and_targets(
    named_states_model: syside.Model, target: str
) -> None:
    program = build_program(
        named_states_model, "StateNames::Flat", target=TARGETS[target]()
    )
    source = to_lf(program)
    assert "initial mode S1_1 {" in source
    assert "mode S1_2 {" in source
    assert "timer t_S1_1(1 msec)" in source
    assert "reaction(t_S1_1) -> reset(S1_2)" in source
    assert '"S1.1"' in source
    assert '"S1.2"' in source
    assert "'S1.1'" not in source


@pytest.mark.parametrize("target", ["python", "c", "uc"])
def test_nested_dotted_names_keep_source_display_paths(
    named_states_model: syside.Model, target: str
) -> None:
    program = build_program(
        named_states_model, "StateNames::Nested", target=TARGETS[target]()
    )
    source = to_lf(program)
    assert "reactor Nested_Outer_1" in source
    assert "c_Outer_1 = new Nested_Outer_1()" in source
    assert "mode Inner_1 {" in source
    assert "mode Inner_2 {" in source
    assert "_Outer_1_sel" in source
    if target == "python":
        assert '"Outer.1." + c_Outer_1.current_state.value' in source
        assert '"Inner.1"' in source
    else:
        assert '"Outer.1.Inner.1"' in source
    assert "'Outer.1'" not in source


@pytest.mark.parametrize("target", ["python", "c", "uc"])
@pytest.mark.parametrize(
    "machine",
    [
        "Collision",
        "GeneratedTimerCollision",
        "InstanceCollision",
        "ReactorCollision",
        "ReactorCaseCollision",
        "ExitCollision",
    ],
)
def test_identifier_collisions_are_disambiguated(
    named_states_model: syside.Model, machine: str, target: str
) -> None:
    program = build_program(
        named_states_model, f"StateNames::{machine}", target=TARGETS[target]()
    )
    assert len({r.name.casefold() for r in program.reactors}) == len(
        program.reactors
    )
    for reactor in program.reactors:
        declarations = [
            *(mode.name for mode in reactor.modes),
            *(timer.name for mode in reactor.modes for timer in mode.timers),
            *(action.name for mode in reactor.modes for action in mode.actions),
            *(
                inst.name
                for mode in reactor.modes
                for inst in mode.instantiations
            ),
        ]
        assert len(declarations) == len(set(declarations))


@pytest.mark.parametrize("target", ["python", "c", "uc"])
def test_same_dotted_state_name_in_distinct_scopes_is_supported(
    named_states_model: syside.Model, target: str
) -> None:
    program = build_program(
        named_states_model, "StateNames::Repeated", target=TARGETS[target]()
    )
    assert [r.name for r in program.reactors] == [
        "Repeated_Outer_1",
        "Repeated_Outer_2",
        "Repeated",
    ]
    assert program.reactors[0].modes[0].name == "Inner_1"
    assert program.reactors[1].modes[0].name == "Inner_1"


@pytest.mark.parametrize("target", ["python", "c", "uc"])
@pytest.mark.parametrize("machine", ["TimerCollision", "AttributeCollision"])
def test_modes_can_share_spelling_with_other_declaration_kinds(
    named_states_model: syside.Model, target: str, machine: str
) -> None:
    program = build_program(
        named_states_model, f"StateNames::{machine}", target=TARGETS[target]()
    )
    assert program.reactor.modes


@pytest.mark.parametrize("target", ["python", "c", "uc"])
def test_valid_original_names_take_priority_over_normalized_names(
    named_states_model: syside.Model, target: str
) -> None:
    program = build_program(
        named_states_model,
        "StateNames::PriorityCollision",
        target=TARGETS[target](),
    )
    assert [mode.name for mode in program.reactor.modes] == [
        "S1_1_02",
        "S1_1",
        "S1_1_01",
    ]
    source = to_lf(program)
    assert "reset(S1_1)" in source
    assert "reset(S1_1_01)" in source
    assert '"S1.1"' in source


@pytest.mark.parametrize("target", ["python", "c", "uc"])
def test_lifecycle_references_use_assigned_variable_names(
    named_states_model: syside.Model, target: str
) -> None:
    program = build_program(
        named_states_model,
        "StateNames::InternalCollision",
        target=TARGETS[target](),
    )
    source = to_lf(program)
    variables = [var.name for var in program.reactor.state_vars]
    assert len(variables) == len(set(variables))
    assert "state_Outer_pending" in variables
    assert "state_Outer_sel" in variables
    assert (
        "state_Outer_busy" in variables
        if target == "python"
        else "state_Outer_c_Outer_busy" in variables
    )
    assert "state_Outer_pending" in source
    assert "state_Outer_sel" in source


@pytest.mark.parametrize("target", ["python", "c", "uc"])
def test_emitted_payload_local_does_not_shadow_target_mode(
    named_states_model: syside.Model, target: str
) -> None:
    program = build_program(
        named_states_model,
        "StateNames::LocalCollision",
        target=TARGETS[target](),
    )
    assert "_value_01" in {mode.name for mode in program.reactor.modes}
    source = to_lf(program)
    assert "reset(_value_01)" in source
    assert '"_value"' in source


@pytest.mark.parametrize("target", ["python", "c", "uc"])
def test_fixed_origin_port_cannot_rename_a_source_signal(
    named_states_model: syside.Model, target: str
) -> None:
    with pytest.raises(
        UnsupportedConstructError,
        match=r"signal channels.*Tick_origin.*collide",
    ):
        build_program(
            named_states_model,
            "StateNames::OriginPortCollision",
            target=TARGETS[target](),
        )


def test_python_observation_escapes_declared_state_names(
    named_states_model: syside.Model,
) -> None:
    program = StateMachineDriver(named_states_model).run(
        "StateNames::EscapedLabels",
        RosettaBuilder("EscapedLabels", observe=True),
    )
    assert isinstance(program, LfProgram)
    messages: list[str] = []
    for reactor in program.reactors:
        reactions = [
            *reactor.reactions,
            *reactor.trailing_reactions,
            *(
                reaction
                for mode in reactor.modes
                for reaction in mode.reactions
            ),
        ]
        for reaction in reactions:
            source = "\n".join(reaction.body)
            compile(source, "generated reaction", "exec")
            for node in ast.walk(ast.parse(source)):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "debug"
                ):
                    messages.append(ast.literal_eval(node.args[0]))
    assert 'entered EscapedLabels.Out"er' in messages
    assert 'exited EscapedLabels.Out"er' in messages
    assert any("a\\b" in message for message in messages)
    assert any("it's" in message for message in messages)


@pytest.mark.parametrize("machine", ["UcFields", "UcAttributeTimer"])
def test_uc_timers_and_state_variables_have_distinct_struct_fields(
    named_states_model: syside.Model, machine: str
) -> None:
    program = build_program(
        named_states_model, f"StateNames::{machine}", target=TARGETS["uc"]()
    )
    reactor = program.reactor
    timers = [timer for mode in reactor.modes for timer in mode.timers]
    variables = {var.name for var in reactor.state_vars}
    assert timers
    assert variables.isdisjoint(timer.name for timer in timers)
    for mode in reactor.modes:
        for timer in mode.timers:
            assert any(
                timer.name in reaction.triggers for reaction in mode.reactions
            )
    source = to_lf(program)
    if machine == "UcFields":
        flag = next(name for name in variables if name.startswith("t_fired"))
        assert f"self->{flag} = true;" in source
    else:
        assert "t_idle" in variables
        assert "self->t_idle == 7" in source


@pytest.mark.parametrize("target", ["python", "c", "uc"])
def test_child_reactor_does_not_shadow_types_companion(
    named_states_model: syside.Model, target: str
) -> None:
    program = build_program(
        named_states_model, "StateNames::TypesHeader", target=TARGETS[target]()
    )
    assert all(
        reactor.name.casefold() != "typesheader_types"
        for reactor in program.reactors
    )


@pytest.mark.parametrize("target", ["python", "c", "uc"])
def test_parallel_state_does_not_claim_a_region_instance_name(
    named_states_model: syside.Model, target: str
) -> None:
    program = build_program(
        named_states_model,
        "StateNames::ParallelInstance",
        target=TARGETS[target](),
    )
    (mode,) = program.reactor.modes
    assert [instance.name for instance in mode.instantiations] == ["c_P", "c_Q"]


def test_fixed_reactor_names_are_compared_without_case(
    named_states_model: syside.Model,
) -> None:
    with pytest.raises(
        UnsupportedConstructError, match="collides across parts"
    ):
        build_part_program(named_states_model, "StateNames::caseSystem")
    with pytest.raises(
        UnsupportedConstructError, match="collide across the rig"
    ):
        compose_exhibits(
            named_states_model,
            "flat",
            (
                ("first", "StateNames::Flat"),
                ("second", "StateNames::Collision"),
            ),
            PreambleNeeds(),
        )


@pytest.mark.parametrize("usage", ["header", "multi"])
def test_fixed_part_reactor_cannot_shadow_types_header(
    named_states_model: syside.Model, usage: str
) -> None:
    with pytest.raises(
        UnsupportedConstructError, match="generated types module"
    ):
        build_part_program(
            named_states_model, f"StateNames::{usage}", target=TARGETS["c"]()
        )


def test_fixed_machine_reactor_cannot_shadow_types_header() -> None:
    needs = PreambleNeeds()
    needs.types_module = "machine"
    with pytest.raises(
        UnsupportedConstructError, match="generated types module"
    ):
        RosettaBuilder("Machine", needs=needs)
