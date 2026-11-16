"""Rosetta's reactor-uc target: program shape, runtime calls and files."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import pytest
from sysmlc.backends import OutputOptions
from sysmlc.errors import SerializationError, UnsupportedConstructError
from sysmlc.sysml.foreign_artifact.base import ForeignArtifact
from sysmlc.sysml.loading import load_model
from sysmlc_models.catalog import model_path
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.backend import RosettaBackend
from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.lifecycle import ENTRY_TRIGGERS
from sysmlc_rosetta.program import (
    LfProgram,
    LogicalAction,
    Mode,
    Reaction,
    Reactor,
)
from sysmlc_rosetta.serialize import to_lf
from sysmlc_rosetta.statements import Stmt, stored
from sysmlc_rosetta.targets.uc import UcTarget, cmake_lists
from tests.conftest import FIXTURES_DIR

if TYPE_CHECKING:
    from collections.abc import Callable


def _uc_program(model_dir: Path, qn: str) -> LfProgram:
    return build_program(load_model(model_dir), qn, target=UcTarget())


def test_flat_machine_is_a_ulf_program() -> None:
    source = to_lf(
        _uc_program(SM_EXAMPLES_DIR / "sm01-helloworld", "SM01::Machine")
    )

    assert source.startswith("preamble {=\n  #include <stdio.h>\n")
    assert "output current_state: {= const char* =}" in source
    assert 'lf_set(current_state, ("idle"));' in source
    assert source.endswith(
        '@platform("native")\n@logging("ERROR")\n'
        "main reactor {\n  m = new Machine()\n}\n"
    )


def test_initial_mode_enters_on_startup_and_reset_once_per_tag() -> None:
    entry = Reaction(ENTRY_TRIGGERS, ("current_state",), ("enter;",))
    reactor = Reactor(
        name="Machine",
        modes=(
            Mode("idle", initial=True, reactions=(entry,)),
            Mode("running", reactions=(entry,)),
        ),
    )

    adapted = UcTarget().adapt(reactor)

    guarded = (
        "if (lf_tag_compare(self->_entered, "
        "env->scheduler->current_tag(env->scheduler)) != 0) {",
        "  self->_entered = env->scheduler->current_tag(env->scheduler);",
        "  enter;",
        "}",
    )
    idle, running = adapted.modes
    assert [(r.triggers, r.body) for r in idle.reactions] == [
        (("startup",), guarded),
        (("reset",), guarded),
    ]
    assert [(r.triggers, r.body) for r in running.reactions] == [
        (("reset",), ("enter;",))
    ]
    (entered,) = adapted.state_vars
    assert (entered.name, entered.type, entered.init, entered.reset) == (
        "_entered",
        "tag_t",
        "NEVER_TAG",
        True,
    )


def test_reactor_without_modes_is_not_adapted() -> None:
    reactor = Reactor(name="Main", reactions=(Reaction(("startup",)),))

    assert UcTarget().adapt(reactor) is reactor


@pytest.mark.parametrize(
    ("spell", "expected"),
    [
        (lambda uc: uc.is_present("c_a._busy"), "lf_is_present(c_a._busy)"),
        (
            lambda uc: uc.arrival(),
            "env->scheduler->current_tag(env->scheduler)",
        ),
        (
            lambda uc: uc.arm_due("_d", "_after_ns"),
            Stmt("self->_d = env->get_elapsed_logical_time(env) + _after_ns;"),
        ),
        (
            lambda uc: uc.schedule("Tick_act", "0", "(t_t){ .n = 1 }"),
            Stmt(
                "lf_schedule(Tick_act, 0, ((t_t){ .n = 1 }));",
                writes=frozenset(("Tick_act",)),
            ),
        ),
        (
            lambda uc: uc.schedule("Tick_act"),
            Stmt("lf_schedule(Tick_act, 0);", writes=frozenset(("Tick_act",))),
        ),
        (
            lambda uc: uc.fail('two "events"'),
            Stmt('throw("two \\"events\\"");'),
        ),
        (lambda uc: uc.request_stop(), Stmt("env->request_shutdown(env, 0);")),
        (lambda uc: uc.log("entered"), Stmt('LF_DEBUG(ENV, "%s", "entered");')),
        (
            lambda uc: uc.value_or("Sig", "0"),
            "(lf_is_present(Sig) ? Sig->value : 0)",
        ),
    ],
)
def test_runtime_calls_are_reactor_uc_s(
    spell: Callable[[UcTarget], object], expected: object
) -> None:
    assert spell(UcTarget()) == expected


def test_run_options_become_main_attributes() -> None:
    program = LfProgram(
        reactors=(Reactor(name="Machine"),),
        target_options=(("fast", "true"), ("timeout", "2 sec")),
        target="uc",
    )

    assert UcTarget().main_opening(program) == [
        '@platform("native")',
        "@fast(true)",
        "@timeout(2 sec)",
        '@logging("ERROR")',
        "main reactor {",
    ]


def test_run_option_without_attribute_is_rejected() -> None:
    program = LfProgram(
        reactors=(Reactor(name="Machine"),),
        target_options=(("keepalive", "true"),),
        target="uc",
    )

    with pytest.raises(SerializationError, match="keepalive"):
        UcTarget().main_opening(program)


def test_uc_build_ships_its_artifacts_and_cmake_compiles_their_sources(
    tmp_path: Path,
) -> None:
    example = SM_EXAMPLES_DIR / "sm15-external-from-metadata"
    external = [
        ForeignArtifact(example / "ramp.py", "python"),
        ForeignArtifact(example / "ramp_impl.c", "c"),
        ForeignArtifact(example / "ramp_impl.h", "c_h"),
    ]
    backend = RosettaBackend().configured({"target": "uc"})
    program = backend.build(
        load_model(example), "SM15ExtImp::Ramp", external=external
    )

    output = backend.generate_output(program, OutputOptions(tmp_path))
    source = output.content(output.paths[0])

    assert [str(path) for path in output.paths] == [
        "RampMain.ulf",
        "Ramp_types.h",
        "ramp_impl.c",
        "ramp_impl.h",
        "CMakeLists.txt",
    ]
    assert "files:" not in source
    assert '#include "ramp_impl.h"' in source
    assert "ramp_impl.c" not in source
    assert output.content(PurePosixPath("CMakeLists.txt")) == cmake_lists(
        "RampMain", ["ramp_impl.c", "ramp_impl.h"]
    )


def test_trigonometry_is_accepted_on_uc() -> None:
    source = to_lf(
        _uc_program(FIXTURES_DIR / "functions", "Functions::Machine")
    )

    assert "cos(" in source
    assert "#include <math.h>" in source


def test_uc_math_functions_use_libm() -> None:
    source = to_lf(
        _uc_program(
            model_path("showcase/charging-station"),
            "ChargingStation::ChargingStationBehavior",
        )
    )

    assert "fmin(self->" in source
    assert "fmax(self->" in source
    assert "sysmlc_min" not in source
    assert "sysmlc_max" not in source


def test_uc_build_writes_cmake_lists() -> None:
    backend = RosettaBackend().configured({"target": "uc"})
    program = backend.build(
        load_model(SM_EXAMPLES_DIR / "sm01-helloworld"), "SM01::Machine"
    )

    output = backend.generate_output(
        program, OutputOptions(Path(), basename="Main")
    )

    cmake = output.content(PurePosixPath("CMakeLists.txt"))
    assert cmake == cmake_lists("Main")
    assert "$ENV{REACTOR_UC_PATH}" in cmake
    assert "target_link_libraries(Main PRIVATE m)" in cmake


def test_uc_default_basename_differs_from_the_reactor_name() -> None:
    backend = RosettaBackend().configured({"target": "uc"})
    program = backend.build(
        load_model(SM_EXAMPLES_DIR / "sm01-helloworld"), "SM01::Machine"
    )

    output = backend.generate_output(program, OutputOptions(Path()))

    assert [str(path) for path in output.paths] == [
        "MachineMain.ulf",
        "Machine_types.h",
        "CMakeLists.txt",
    ]
    assert output.content(PurePosixPath("CMakeLists.txt")) == cmake_lists(
        "MachineMain"
    )


def test_uc_reactor_name_as_basename_means_the_default() -> None:
    backend = RosettaBackend().configured({"target": "uc"})
    program = backend.build(
        load_model(SM_EXAMPLES_DIR / "sm01-helloworld"), "SM01::Machine"
    )

    output = backend.generate_output(
        program, OutputOptions(Path(), basename="Machine")
    )

    assert str(output.paths[0]) == "MachineMain.ulf"


@pytest.mark.parametrize("basename", ["machine", "MACHINE"])
def test_uc_basename_clashing_with_a_reactor_is_rejected(
    basename: str,
) -> None:
    backend = RosettaBackend().configured({"target": "uc"})
    program = backend.build(
        load_model(SM_EXAMPLES_DIR / "sm01-helloworld"), "SM01::Machine"
    )

    with pytest.raises(SerializationError, match="up to case"):
        backend.generate_output(
            program, OutputOptions(Path(), basename=basename)
        )


@pytest.mark.parametrize("target", ["python", "c"])
def test_other_targets_write_no_cmake_lists(target: str) -> None:
    backend = RosettaBackend().configured({"target": target})
    program = backend.build(
        load_model(SM_EXAMPLES_DIR / "sm01-helloworld"), "SM01::Machine"
    )

    output = backend.generate_output(program, OutputOptions(Path()))

    assert "CMakeLists.txt" not in [str(path) for path in output.paths]


def test_cmake_lists_wraps_lfc_s_generated_build() -> None:
    cmake = cmake_lists("Main")

    assert (
        'COMMAND "${SYSMLC_LFC}" -n "${CMAKE_CURRENT_SOURCE_DIR}/Main.ulf"'
        in cmake
    )
    assert (
        'add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/src-gen/Main" lf)'
        in cmake
    )
    assert "CMAKE_CONFIGURE_DEPENDS" in cmake
    assert "target_sources" not in cmake


def test_cmake_lists_compiles_external_sources() -> None:
    cmake = cmake_lists("Main", ["ramp.c", "ramp.h", "lib/more.c"])

    assert (
        "target_sources(Main PRIVATE\n"
        '  "${CMAKE_CURRENT_SOURCE_DIR}/ramp.c"\n'
        '  "${CMAKE_CURRENT_SOURCE_DIR}/lib/more.c"\n'
        ")"
    ) in cmake


def test_drain_of_a_composite_state_stays_inside_its_mode() -> None:
    program = _uc_program(
        model_path("showcase/microwave"), "Microwave::MicrowaveBehavior"
    )
    cooking = next(r for r in program.reactors if r.name.endswith("_cooking"))

    *others, heating = cooking.modes
    assert heating.name == "heating"
    assert heating.initial
    assert [mode.name for mode in others] == ["paused", "done"]
    effects = [r.effects for r in heating.reactions]
    assert ("c_heater._exit", "_exited") in effects
    assert ("c_turntable._exit",) in effects
    (handler,) = cooking.trailing_reactions
    assert handler.triggers == ("_exit",)
    assert not any("c_" in effect for effect in handler.effects)


def test_drains_of_sibling_composite_states_are_rejected() -> None:
    with pytest.raises(UnsupportedConstructError, match="one such state"):
        _uc_program(
            FIXTURES_DIR / "uc-sibling-composites", "SiblingComposites::Machine"
        )


def test_actions_hold_as_many_events_as_statements_schedule() -> None:
    reactor = Reactor(
        name="Machine",
        actions=(LogicalAction("_change_act"), LogicalAction("Tick_act")),
        reactions=(
            Reaction(
                ("startup",),
                body=stored(
                    (
                        Stmt("x;", writes=frozenset(("_change_act",))),
                        Stmt("y;", writes=frozenset(("_change_act",))),
                    )
                ),
            ),
        ),
    )

    declared = UcTarget().declare(reactor)

    assert [(a.name, a.pending) for a in declared.actions] == [
        ("_change_act", 2),
        ("Tick_act", 1),
    ]
    source = to_lf(LfProgram(reactors=(declared,), target="uc"))
    assert (
        "  @max_pending_events(2)\n  logical action _change_act\n"
        "  logical action Tick_act\n"
    ) in source
