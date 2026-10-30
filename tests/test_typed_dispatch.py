"""Typed dispatch metadata and unsupported constraint diagnostics."""

import pytest
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program
from tests.conftest import FIXTURES_DIR


def test_value_bound_accept_remains_explicitly_unsupported() -> None:
    model = load_model(FIXTURES_DIR / "typed-dispatch")
    with pytest.raises(UnsupportedConstructError, match="value-bound"):
        build_program(model, "TypedDispatch::UnsupportedBound")


def test_external_type_mapping_is_preserved_without_send_actions() -> None:
    model = load_model(FIXTURES_DIR / "typed-dispatch")
    program = build_program(model, "TypedDispatch::External")
    assert program.resolve_signal("TypedDispatch::Base") == "Base"
    assert program.resolve_signal("Child") == "Child"
    assert dict(program.signal_channels) == {
        "TypedDispatch::Base": "Base",
        "TypedDispatch::Child": "Child",
    }
    with pytest.raises(ValueError, match="unknown or ambiguous"):
        program.resolve_signal("Other")
