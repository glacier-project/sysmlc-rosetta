import pytest

from sysmlc.backends.rosetta.program import (
    LfProgram,
    LogicalAction,
    Mode,
    Parameter,
    Reaction,
    Reactor,
    StateVar,
    Timer,
)
from sysmlc.backends.rosetta.serialize import render_duration, to_lf


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (5.0, "5 sec"),
        (120.0, "120 sec"),
        (2.5, "2500 msec"),
        (0.001, "1 msec"),
        (0.000001, "1 usec"),
        (0.0000005, "500 nsec"),
        (0.0, "0 sec"),
    ],
)
def test_render_duration_picks_largest_exact_unit(seconds, expected) -> None:
    assert render_duration(seconds) == expected


def test_to_lf_renders_full_program():
    program = LfProgram(
        reactor=Reactor(
            name="Machine",
            inputs=("Tick",),
            outputs=("current_state",),
            state_vars=(StateVar("enabled", "True"),),
            actions=(LogicalAction("Ping_act"),),
            reactions=(Reaction(("startup",), (), ("self.enabled = True",)),),
            modes=(
                Mode(
                    name="idle",
                    initial=True,
                    reactions=(
                        Reaction(
                            ("reset", "startup"),
                            ("current_state",),
                            ('current_state.set("idle")',),
                        ),
                        # Body lines carry their own Python indentation;
                        # the serializer adds the uniform LF pad on top.
                        Reaction(
                            ("Tick",),
                            ("reset(running)",),
                            ("if self.enabled:", "    running.set()"),
                        ),
                    ),
                ),
                Mode(
                    name="running",
                    timers=(Timer("t0", "5 sec"),),
                    actions=(LogicalAction("after0_act"),),
                    reactions=(
                        Reaction(
                            ("reset", "startup"),
                            ("current_state",),
                            ('current_state.set("running")',),
                        ),
                    ),
                ),
            ),
        ),
        preamble=("from types import SimpleNamespace",),
    )
    assert to_lf(program) == (
        "target Python\n"
        "\n"
        "preamble {=\n"
        "  from types import SimpleNamespace\n"
        "=}\n"
        "\n"
        "reactor Machine {\n"
        "  input Tick\n"
        "  output current_state\n"
        "  state enabled = {= True =}\n"
        "  logical action Ping_act\n"
        "  reaction(startup) {=\n"
        "    self.enabled = True\n"
        "  =}\n"
        "  initial mode idle {\n"
        "    reaction(reset, startup) -> current_state {=\n"
        '      current_state.set("idle")\n'
        "    =}\n"
        "    reaction(Tick) -> reset(running) {=\n"
        "      if self.enabled:\n"
        "          running.set()\n"
        "    =}\n"
        "  }\n"
        "  mode running {\n"
        "    timer t0(5 sec)\n"
        "    logical action after0_act\n"
        "    reaction(reset, startup) -> current_state {=\n"
        '      current_state.set("running")\n'
        "    =}\n"
        "  }\n"
        "}\n"
        "\n"
        "main reactor {\n"
        "  m = new Machine()\n"
        "}\n"
    )


def test_reactor_parameters_render_in_signature() -> None:
    program = LfProgram(
        reactor=Reactor(
            name="M",
            parameters=(
                Parameter("setpoint", "21.0"),
                Parameter("hysteresis", "0.5"),
            ),
            modes=(
                Mode(
                    name="only",
                    initial=True,
                    reactions=(Reaction(("reset", "startup"), (), ("pass",)),),
                ),
            ),
        )
    )
    text = to_lf(program)
    assert "reactor M(setpoint = {= 21.0 =}, hysteresis = {= 0.5 =}) {" in text
