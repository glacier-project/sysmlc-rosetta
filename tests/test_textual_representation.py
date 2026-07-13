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
