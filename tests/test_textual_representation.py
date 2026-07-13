from __future__ import annotations

import pytest

from sysmlc.backends.rosetta.backend import RosettaBackend
from sysmlc.backends.rosetta.program import LfProgram
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.backends.rosetta.textual_representation import extract_textual
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR
from tests.backends.test_sm_examples import SM_EXAMPLES_DIR


def test_starred_unpacking_line_survives_verbatim() -> None:
    model = load_model(FIXTURES_DIR / "rep-star")
    result = extract_textual(model, "StarProof::unpack_last")
    assert result is not None
    _stem, _names, lines = result
    assert "    *head, tail = values" in lines


def test_package_rep_collected_as_module_scaffolding() -> None:
    model = load_model(FIXTURES_DIR / "rep-package-scaffolding")
    result = extract_textual(model, "Scaffold::quadruple")
    assert result is not None
    _stem, _names, lines = result
    assert "FACTOR = 2.0" in lines
    assert "def quadruple(x):" in lines
    # The package rep is the module preamble: it precedes the functions.
    assert lines.index("FACTOR = 2.0") < lines.index("def quadruple(x):")


def test_python_rep_outside_package_or_calc_def_is_rejected() -> None:
    model = load_model(FIXTURES_DIR / "rep-on-action")
    with pytest.raises(UnsupportedConstructError, match="logIt"):
        extract_textual(model, "RepOnAction::logIt")


def test_invalid_python_in_a_rep_body_names_the_calc_def() -> None:
    model = load_model(FIXTURES_DIR / "rep-bad-syntax")
    with pytest.raises(UnsupportedConstructError, match="BadSyntax::broken"):
        extract_textual(model, "BadSyntax::broken")


def test_extracted_names_are_the_rep_backed_calc_defs_only() -> None:
    # The scaffolding helper `_twice` is module-internal: only calc defs
    # back SysML calls, so only their names are importable.
    model = load_model(FIXTURES_DIR / "rep-package-scaffolding")
    result = extract_textual(model, "Scaffold::quadruple")
    assert result is not None
    _stem, names, _lines = result
    assert names == frozenset({"quadruple"})


def test_two_python_reps_on_one_calc_def_are_rejected() -> None:
    model = load_model(FIXTURES_DIR / "rep-two-bodies")
    with pytest.raises(UnsupportedConstructError, match="more than one Python"):
        extract_textual(model, "TwoBodies::double")


def test_def_name_mismatch_names_both_sides() -> None:
    model = load_model(FIXTURES_DIR / "rep-name-mismatch")
    with pytest.raises(
        UnsupportedConstructError,
        match="defines 'restrictAngle' but the calc def is named",
    ):
        extract_textual(model, "NameMismatch::restrict_angle")


def test_conflicting_defs_across_packages_are_rejected() -> None:
    model = load_model(FIXTURES_DIR / "rep-name-collision")
    with pytest.raises(
        UnsupportedConstructError, match="different implementations"
    ):
        extract_textual(model, "Collision::step")


def test_scaffolding_def_shadowing_a_calc_def_is_rejected() -> None:
    model = load_model(FIXTURES_DIR / "rep-scaffolding-shadow")
    with pytest.raises(
        UnsupportedConstructError, match="different implementations"
    ):
        extract_textual(model, "Shadow::gain")


def test_identical_duplicate_helpers_stay_allowed() -> None:
    model = load_model(FIXTURES_DIR / "rep-duplicate-identical")
    result = extract_textual(model, "DupA::use_sign")
    assert result is not None
    _stem, names, _lines = result
    assert names == frozenset({"use_sign"})


def test_sm15_rep_backs_the_calc_call_with_a_generated_module() -> None:
    # Twin of sm15-external: same Ramp machine, but the calc body comes
    # from the rep instead of a --python file, with no external given.
    model = load_model(SM_EXAMPLES_DIR / "sm15-rep")
    program = RosettaBackend().build(model, "SM15Rep::Ramp")
    assert isinstance(program, LfProgram)
    assert program.external_module_name == "Ramp_impl"
    assert "def step(x, dt):" in program.external_module_lines
    assert "from Ramp_impl import step" in program.preamble
    text = to_lf(program)
    assert '"Ramp_impl.py"' in text
    assert "step(self.x, 0.1)" in text
