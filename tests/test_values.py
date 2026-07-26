from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sysmlc.sysml.loading import load_model
from sysmlc.values import (
    ValuesError,
    configure_model,
    load_values,
    select_values,
)

from sysmlc_rosetta.builder import build_program

if TYPE_CHECKING:
    from pathlib import Path

    from sysmlc_rosetta.program import LfProgram

# A self-contained configurable machine: scalar parameters, an initial
# value, a string, a boolean, a composite with type-level defaults, and a
# quantity. `PointFixed` binds its field with `=` (fixed by the language).
MODEL = """package VPkg {
    private import ScalarValues::*;
    private import SI::*;
    private import ISQ::*;

    attribute def Point {
        attribute x : Real default 0.5;
        attribute y : Real default 1.0;
    }

    attribute def PointFixed {
        attribute x : Real = 0.5;
    }

    state def Machine {
        in attribute setpoint : Real default 21.0;
        attribute bare : Real;
        attribute temperature : Real := 18.0;
        attribute label : String := "off";
        attribute armed : Boolean := false;
        attribute pt : Point;
        attribute fixed : PointFixed;
        attribute pickDuration : DurationValue default 2 [min];
        attribute warmUp : DurationValue default 2.0 [min];
        entry;
            then idle;
        state idle;
    }
}
"""

QN = "VPkg::Machine"


def _configured(tmp_path: Path, values: dict) -> LfProgram:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "machine.sysml").write_text(MODEL)
    model = load_model(model_dir)
    configured = configure_model(model, QN, values)
    return build_program(configured, QN)


def _state_var(program: LfProgram, name: str) -> str:
    (init,) = [v.init for v in program.reactor.state_vars if v.name == name]
    return init


# -- load / select ---------------------------------------------------------


def test_load_values_reads_nested_mapping(tmp_path: Path) -> None:
    f = tmp_path / "values.yaml"
    f.write_text("Thermostat:\n  Thermostat:\n    setpoint: 23.0\n")
    assert load_values(f) == {"Thermostat": {"Thermostat": {"setpoint": 23.0}}}


def test_load_values_empty_file_is_empty_mapping(tmp_path: Path) -> None:
    f = tmp_path / "values.yaml"
    f.write_text("")
    assert load_values(f) == {}


def test_load_values_rejects_non_mapping(tmp_path: Path) -> None:
    f = tmp_path / "values.yaml"
    f.write_text("- a\n- b\n")
    with pytest.raises(ValuesError, match="mapping"):
        load_values(f)


def test_load_values_rejects_bad_yaml(tmp_path: Path) -> None:
    f = tmp_path / "values.yaml"
    f.write_text("a: [unclosed\n")
    with pytest.raises(ValuesError, match="cannot parse"):
        load_values(f)


def test_select_values_walks_qualified_name() -> None:
    tree: dict[str, object] = {
        "Pkg": {"Machine": {"setpoint": 23.0, "pt": {"x": 0.7}}}
    }
    assert select_values(tree, "Pkg::Machine") == {
        "setpoint": 23.0,
        "pt": {"x": 0.7},
    }


def test_select_values_missing_element_is_empty() -> None:
    tree: dict[str, object] = {"Pkg": {"Machine": {"setpoint": 23.0}}}
    assert select_values(tree, "Pkg::Other") == {}
    assert select_values(tree, "Other::Machine") == {}


def test_select_values_rejects_scalar_entry() -> None:
    tree: dict[str, object] = {"Pkg": {"Machine": 5}}
    with pytest.raises(ValuesError, match="map attribute names"):
        select_values(tree, "Pkg::Machine")


def test_select_values_rejects_non_scalar_leaf() -> None:
    tree: dict[str, object] = {"Pkg": {"Machine": {"setpoint": [1, 2]}}}
    with pytest.raises(ValuesError, match="scalar"):
        select_values(tree, "Pkg::Machine")


# -- configure_model: source edits + reload ---------------------------------


def test_parameter_default_is_overridden(tmp_path: Path) -> None:
    program = _configured(tmp_path, {"setpoint": 23.5})
    (setpoint,) = [
        p for p in program.reactor.parameters if p.name == "setpoint"
    ]
    assert setpoint.default == "23.5"


def test_initial_value_is_overridden(tmp_path: Path) -> None:
    program = _configured(tmp_path, {"temperature": 25.0})
    assert _state_var(program, "temperature") == "25.0"


def test_string_override_renders_double_quoted(tmp_path: Path) -> None:
    program = _configured(tmp_path, {"label": "on"})
    assert _state_var(program, "label") == '"on"'


def test_boolean_override_renders_sysml_then_python(tmp_path: Path) -> None:
    program = _configured(tmp_path, {"armed": True})
    assert _state_var(program, "armed") == "True"


def test_composite_override_redefines_per_usage(tmp_path: Path) -> None:
    # `pt : Point` declares nothing locally: the override must inject a
    # usage-local redefinition, never touch Point's own defaults.
    program = _configured(tmp_path, {"pt": {"x": 0.7}})
    assert _state_var(program, "pt") == "Point(x=0.7, y=1.0)"


def test_fixed_binding_cannot_be_overridden(tmp_path: Path) -> None:
    # PointFixed binds x with `=`: fixed by the model — the same rule the
    # language applies to redefinitions, enforced via FeatureValue flags.
    with pytest.raises(ValuesError, match="bound with"):
        _configured(tmp_path, {"fixed": {"x": 0.9}})


def test_missing_initializer_is_created(tmp_path: Path) -> None:
    # `bare` declares no value: the override creates the FeatureValue
    # in place (impossible before the in-place rework).
    program = _configured(tmp_path, {"bare": 7.5})
    assert _state_var(program, "bare") == "7.5"


def test_quantity_number_keeps_model_unit(tmp_path: Path) -> None:
    # 5 means 5 [min]; quantities bind as SI floats.
    program = _configured(tmp_path, {"pickDuration": 5})
    assert _state_var(program, "pickDuration") == "300.0"


def test_quantity_sysml_string_converts_to_model_units(
    tmp_path: Path,
) -> None:
    # 90 [s] = 1.5 [min]: converted into the model's declared unit and
    # set on its rational magnitude; quantities bind as SI floats.
    program = _configured(tmp_path, {"warmUp": "90 [s]"})
    assert _state_var(program, "warmUp") == "90.0"


def test_quantity_integral_conversion_fits_integer_literal(
    tmp_path: Path,
) -> None:
    # 120 [s] = 2 [min]: integral, so the integer magnitude accepts it.
    program = _configured(tmp_path, {"pickDuration": "120 [s]"})
    assert _state_var(program, "pickDuration") == "120.0"


def test_quantity_fractional_into_integer_literal_is_rejected(
    tmp_path: Path,
) -> None:
    # 90 [s] = 1.5 [min], but the model declares the magnitude as the
    # integer 2: parsed literals can be mutated, never replaced.
    with pytest.raises(ValuesError, match="rational magnitude"):
        _configured(tmp_path, {"pickDuration": "90 [s]"})


def test_scalar_kind_change_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValuesError, match="kind"):
        _configured(tmp_path, {"temperature": "hot"})


def test_quantity_unit_kind_mismatch_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValuesError, match="unit kind"):
        _configured(tmp_path, {"pickDuration": "3 [m]"})


def test_unknown_attribute_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValuesError, match="does not match an attribute"):
        _configured(tmp_path, {"setpoit": 1.0})


def test_unknown_composite_field_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValuesError, match="does not match a field"):
        _configured(tmp_path, {"pt": {"z": 1.0}})


def test_scalar_for_composite_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValuesError, match="composite"):
        _configured(tmp_path, {"pt": 1.0})


def test_mapping_for_scalar_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValuesError, match="not a composite"):
        _configured(tmp_path, {"temperature": {"x": 1.0}})


def test_empty_values_return_original_model(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "machine.sysml").write_text(MODEL)
    model = load_model(model_dir)
    assert configure_model(model, QN, {}) is model
