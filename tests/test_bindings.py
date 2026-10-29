from __future__ import annotations

import re

import pytest
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model
from sysmlc.values import configure_model

from sysmlc_rosetta.builder import build_program
from tests.conftest import FIXTURES_DIR

BINDINGS_DIR = FIXTURES_DIR / "bindings"


def test_constant_bindings_render_as_unassigned_state() -> None:
    program = build_program(load_model(BINDINGS_DIR), "Bindings::Machine")
    reactor = program.reactor
    assert {v.name: v.init for v in reactor.state_vars} == {
        "k": "1",
        "ratio": "-2.5",
        "setting": "Mode.busy",
        "light": "Light.green",
        "positive": "+4",
        "holdTime": "2.0",
        "limit": "3",
        "a": "1",
        "seen": "0",
    }
    lines = [
        line
        for mode in reactor.modes
        for reaction in mode.reactions
        for line in reaction.body
    ]
    assert "self.a = 5" in lines
    assert "self.seen = self.a + self.k + self.limit" in lines
    assert (
        "if self.a == 5 and self.k == 1 and self.setting == Mode.busy"
        " and self.light == Light.green:"
    ) in lines
    constants = (
        "k",
        "ratio",
        "setting",
        "light",
        "positive",
        "holdTime",
        "limit",
    )
    assert not [
        line
        for line in lines
        for name in constants
        if line.startswith(f"self.{name} = ")
    ]


def test_configured_bound_default_is_the_new_constant() -> None:
    model = configure_model(
        load_model(BINDINGS_DIR), "Bindings::Machine", {"limit": 4}
    )
    program = build_program(model, "Bindings::Machine")
    initializers = {v.name: v.init for v in program.reactor.state_vars}
    assert initializers["limit"] == "4"


@pytest.mark.parametrize(
    ("qn", "diagnostic"),
    [
        (
            "Bindings::DynamicBinding",
            "attribute 'b' is bound with '=' to an expression that is not "
            "a literal constant",
        ),
        (
            "Bindings::BoundWrite",
            "the `entry` action of state 'idle' assigns "
            "'Bindings::BoundWrite::k', which is bound with '='",
        ),
    ],
)
def test_unsupported_binding_is_rejected(qn: str, diagnostic: str) -> None:
    with pytest.raises(UnsupportedConstructError, match=re.escape(diagnostic)):
        build_program(load_model(BINDINGS_DIR), qn)
