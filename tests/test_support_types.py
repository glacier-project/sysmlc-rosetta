from __future__ import annotations

import sys
from types import ModuleType
from typing import TYPE_CHECKING

from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


MODEL = """package SupportTypes {
    private import ScalarValues::*;
    attribute def Point {
        attribute x : Real default 0.5;
        attribute y : Real default 1.0;
    }
    state def Machine {
        attribute point : Point;
        entry; then idle;
        state idle;
    }
}
"""


def test_generated_type_supports_external_constructor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "model.sysml").write_text(MODEL)
    program = build_program(load_model(tmp_path), "SupportTypes::Machine")
    assert program.types_module_name is not None
    module = ModuleType(program.types_module_name)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec("\n".join(program.types_module_lines), module.__dict__)
    original = module.Point(x=0.1, y=0.2)
    # Foreign support functions reconstruct values through their runtime type.
    result = type(original)(x=original.x + 1.0, y=original.y)
    assert isinstance(result, module.Point)
    assert result is not original
    assert result.x == 1.1
    assert result.y == 0.2
    assert original.x == 0.1
    assert module.Point().y == 1.0
