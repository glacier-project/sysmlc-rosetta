"""Rosetta's C target: rendering, declarations, types and generated files."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import syside
from sysmlc.backends import OutputOptions
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.foreign_artifact.base import ForeignArtifact
from sysmlc.sysml.loading import load_model
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.backend import RosettaBackend
from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.c_types import type_name
from sysmlc_rosetta.program import LfProgram, Reactor
from sysmlc_rosetta.serialize import to_lf
from sysmlc_rosetta.statements import If, Pass, Stmt, nest
from sysmlc_rosetta.targets.c import CTarget

if TYPE_CHECKING:
    from pathlib import Path


def _c_program(example: str, qn: str) -> LfProgram:
    model = load_model(SM_EXAMPLES_DIR / example)
    return build_program(model, qn, target=CTarget())


def test_flat_machine_is_a_c_program() -> None:
    source = to_lf(_c_program("sm01-helloworld", "SM01::Machine"))

    assert source.startswith('target C {\n  files: ["Machine_types.h"],\n}')
    assert '#include "Machine_types.h"' in source
    assert "output current_state: string" in source
    assert 'lf_set(current_state, ("idle"));' in source
    assert "lf_set_mode(running);" in source


def test_child_reactors_announce_their_full_path() -> None:
    source = to_lf(_c_program("sm08-nested-composite", "SM08::MachineNested"))

    assert 'lf_set(current_state, ("running.warming"));' in source
    assert "lf_set(current_state, (c_running.current_state->value));" in source


def test_conditional_renders_with_braces_and_drops_pass() -> None:
    body = (
        If(
            (("a", (Stmt("one;"),)), ("b", (Pass(),))),
            orelse=(nest("c", (Stmt("two;"),)),),
        ),
    )

    assert CTarget().render(body) == (
        "if (a) {",
        "  one;",
        "} else if (b) {",
        "} else {",
        "  if (c) {",
        "    two;",
        "  }",
        "}",
    )


def test_a_local_is_declared_where_first_assigned() -> None:
    c = CTarget()
    body = (
        c.bind_after("1"),
        c.bind_after("2"),
        nest("a", (c.bind_tag("lf_tag()"),)),
        c.bind_tag("NEVER_TAG"),
    )

    assert c.render(body) == (
        "interval_t _after_ns = 1;",
        "_after_ns = 2;",
        "if (a) {",
        "  tag_t _tag = lf_tag();",
        "}",
        "tag_t _tag = NEVER_TAG;",
    )


def test_busy_children_are_one_flag_each() -> None:
    c = CTarget()
    insts = ("c_a", "c_b", "c_c")

    flags = c.state_vars("heating", "busy", reset=True, insts=insts)

    assert [var.name for var in flags] == [
        "_heating_c_a_busy",
        "_heating_c_b_busy",
        "_heating_c_c_busy",
    ]
    assert {var.type for var in flags} == {"bool"}
    assert c.busy_any("heating", insts) == (
        "(self->_heating_c_a_busy || self->_heating_c_b_busy || "
        "self->_heating_c_c_busy)"
    )


@pytest.mark.parametrize(
    ("port", "ctype"),
    [
        ("current_state", "string"),
        ("c_sim_current_state", "string"),
        ("completed", "bool"),
        ("exit_0_done", "bool"),
        ("_busy", "tag_t"),
        ("Tick_origin", "tag_t"),
        ("Tick_consumed", "sysmlc_consumption_t"),
        ("Tick", "bool"),
    ],
)
def test_ports_are_typed_by_their_role(port: str, ctype: str) -> None:
    assert CTarget().port_type(port) == ctype


def test_payload_types_follow_the_qualified_name() -> None:
    program = _c_program("sm19-payload-construction", "SM19::MachinePositional")
    model = load_model(SM_EXAMPLES_DIR / "sm19-payload-construction")
    data = next(
        definition
        for definition in model.elements(
            syside.ItemDefinition, include_subtypes=True
        )
        if definition.name == "Data"
    )
    (header,) = (text for name, text in program.support_files)

    assert type_name(data) == "sm19_data_t"
    assert "} sm19_data_t;" in header
    assert "input Data: sm19_data_t" in to_lf(program)
    assert "lf_schedule_copy(Data_act, 0, &((sm19_data_t){" in to_lf(program)


def test_quantities_are_doubles() -> None:
    source = to_lf(
        _c_program("sm13-time-trigger", "SM13::MachineAfterAttribute")
    )

    assert "state pickDuration: double = {= 120.0 =}" in source


def test_enumerations_are_c_enums_with_names() -> None:
    program = _c_program("sm18-enum-literals", "SM18::MachineStringEnum")
    (header,) = (text for _, text in program.support_files)

    assert "typedef enum { SM18_LIGHT_COLOR_RED," in header
    assert "sm18_light_color_names[]" in header
    assert "self->c = SM18_LIGHT_COLOR_GREEN;" in to_lf(program)


def test_backend_selects_its_target() -> None:
    backend = RosettaBackend()

    (option,) = backend.build_options()
    python = backend.configured({"target": "python"})
    c = backend.configured({"target": "c"})

    assert (option.name, option.choices, option.default) == (
        "target",
        ("python", "c", "uc"),
        "python",
    )
    assert isinstance(python, RosettaBackend)
    assert isinstance(c, RosettaBackend)
    assert c.target == "c"
    assert c.owns_external_delivery()
    assert not python.owns_external_delivery()


def test_configured_backend_consumes_only_its_target_languages() -> None:
    # The CLI resolves and copies artifacts by these languages, so a Python
    # build must not pick up a model's C artifacts.
    backend = RosettaBackend()

    python = backend.configured({"target": "python"})
    c = backend.configured({"target": "c"})

    assert backend.supported_foreign_artifact_languages() == {
        "python",
        "c",
        "c_h",
    }
    assert python.supported_foreign_artifact_languages() == {"python"}
    assert c.supported_foreign_artifact_languages() == {"c", "c_h"}


@pytest.mark.parametrize(
    "name", ["default", "switch", "base", "NEVER", "MSEC", "fprintf", "lf_x"]
)
def test_reserved_names_are_rejected(name: str) -> None:
    reactor = Reactor(name="Machine", inputs=(name,))

    with pytest.raises(UnsupportedConstructError, match="C keywords"):
        CTarget().declare(reactor)


def test_payload_of_channels_with_different_types_is_rejected() -> None:
    c = CTarget()
    c.types._signals.update({"Base": "base_t", "Sub": "sub_t"})

    with pytest.raises(UnsupportedConstructError, match="different C types"):
        c.unpack_value(("Base", "Sub"))


def test_field_less_payload_binds_nothing() -> None:
    c = CTarget()
    c.register_signal("Alarm", None)

    assert c.bind_value("Alarm->value", "Alarm") == ()
    assert c.unpack_value(("Alarm",)) == ()
    assert ".value.Alarm" not in c.pending_hold("_p", 0, "_value", "Alarm").text


def test_c_build_ships_its_artifacts_and_compiles_their_sources(
    tmp_path: Path,
) -> None:
    example = SM_EXAMPLES_DIR / "sm15-external-from-metadata"
    external = [
        ForeignArtifact(example / "ramp.py", "python"),
        ForeignArtifact(example / "ramp_impl.c", "c"),
        ForeignArtifact(example / "ramp_impl.h", "c_h"),
    ]
    backend = RosettaBackend("c")
    program = backend.build(
        load_model(example), "SM15ExtImp::Ramp", external=external
    )

    output = backend.generate_output(program, OutputOptions(tmp_path))
    source = output.content(output.paths[0])

    assert [str(path) for path in output.paths] == [
        "Ramp.lf",
        "Ramp_types.h",
        "Ramp_types.cmake",
        "ramp_impl.c",
        "ramp_impl.h",
    ]
    assert 'files: ["Ramp_types.h", "ramp_impl.c", "ramp_impl.h"],' in source
    assert 'cmake-include: "Ramp_types.cmake",' in source
    assert '#include "ramp_impl.h"' in source
    assert "self->x = step(self->x, 0.1);" in source


def test_unbacked_calc_def_is_a_declared_extern() -> None:
    program = _c_program("sm15-external", "SM15::Ramp")
    (header,) = (text for _, text in program.support_files)

    assert "extern double sm15_p_step(double x, double dt);" in header
    assert "self->x = sm15_p_step(self->x, 0.1);" in to_lf(program)


def test_backed_function_gets_a_prototype() -> None:
    # Without it, a C file passed without its header is called through an
    # implicit declaration that reads a double as an int.
    example = SM_EXAMPLES_DIR / "sm15-external-from-metadata"
    program = RosettaBackend("c").build(
        load_model(example),
        "SM15ExtImp::Ramp",
        external=[ForeignArtifact(example / "ramp_impl.c", "c")],
    )
    assert isinstance(program, LfProgram)
    header = dict(program.support_files)["Ramp_types.h"]

    assert "extern double step(double x, double dt);" in header


def test_nested_structured_initializer_is_one_compound_literal() -> None:
    source = to_lf(
        _c_program("sm05-chained-references", "SM05::MachineChainNested")
    )

    assert "(sm05_box_t){ .inner = { .z = 0.25 } }" in source
