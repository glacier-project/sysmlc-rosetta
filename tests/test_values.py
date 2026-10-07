from __future__ import annotations

from typing import TYPE_CHECKING

from sysmlc.sysml.loading import load_model
from sysmlc.values import configure_model

from sysmlc_rosetta.builder import build_program

if TYPE_CHECKING:
    from pathlib import Path


MODEL = """package Configured {
    private import ScalarValues::*;
    private import SI::*;
    private import ISQ::*;
    attribute def Point {
        attribute x : Real default 0.5;
        attribute y : Real default 1.0;
    }
    state def Machine {
        in attribute setpoint : Real default 21.0;
        attribute temperature : Real := 18.0;
        attribute label : String := "off";
        attribute armed : Boolean := false;
        attribute pt : Point;
        attribute warmUp : DurationValue default 2.0 [min];
        entry; then idle;
        state idle;
    }
}
"""


def test_configured_values_render_as_lf_python(tmp_path: Path) -> None:
    (tmp_path / "model.sysml").write_text(MODEL)
    model = configure_model(
        load_model(tmp_path),
        "Configured::Machine",
        {
            "setpoint": 23.5,
            "temperature": 25.0,
            "label": "on",
            "armed": True,
            "pt": {"x": 0.7},
            "warmUp": "90 [s]",
        },
    )
    program = build_program(model, "Configured::Machine")
    assert {p.name: p.default for p in program.reactor.parameters} == {
        "setpoint": "23.5"
    }
    initializers = {v.name: v.init for v in program.reactor.state_vars}
    assert initializers["temperature"] == "25.0"
    assert initializers["label"] == '"on"'
    assert initializers["armed"] == "True"
    assert initializers["pt"] == "Point(x=0.7, y=1.0)"
    assert initializers["warmUp"] == "90.0"
