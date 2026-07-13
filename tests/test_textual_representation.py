from __future__ import annotations

from sysmlc.backends.rosetta.textual_representation import extract_textual
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR


def test_starred_unpacking_line_survives_verbatim() -> None:
    model = load_model(FIXTURES_DIR / "rep-star")
    result = extract_textual(model, "StarProof::unpack_last")
    assert result is not None
    _stem, _names, lines = result
    assert "    *head, tail = values" in lines
