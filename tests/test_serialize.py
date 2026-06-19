from typing import Any

import pytest

from sysmlc.backends.rosetta.program import (
    Connection,
    Instantiation,
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


def make_reactors(**kwargs: Any) -> tuple[Reactor, ...]:
    """Wrap a single machine reactor for ``LfProgram.reactors``."""
    return (Reactor(**kwargs),)


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
def test_render_duration_picks_largest_exact_unit(
    seconds: float, expected: str
) -> None:
    assert render_duration(seconds) == expected


def test_to_lf_renders_full_program() -> None:
    program = LfProgram(
        reactors=make_reactors(
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
        reactors=make_reactors(
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


def _announce(state: str) -> Reaction:
    return Reaction(
        ("reset", "startup"),
        ("current_state",),
        (f'current_state.set("{state}")',),
    )


def test_multiple_reactors_render_in_order_with_main_last() -> None:
    child = Reactor(
        name="Machine_running",
        outputs=("completed", "current_state"),
        modes=(
            Mode(
                name="warming",
                initial=True,
                reactions=(_announce("warming"),),
            ),
        ),
    )
    machine = Reactor(
        name="Machine",
        inputs=("Tick",),
        outputs=("current_state",),
        modes=(
            Mode(name="idle", initial=True, reactions=(_announce("idle"),)),
            Mode(
                name="running",
                instantiations=(Instantiation("c_running", "Machine_running"),),
                connections=(Connection("Tick", "c_running.Tick"),),
                reactions=(_announce("running"),),
            ),
        ),
    )
    text = to_lf(LfProgram(reactors=(child, machine)))
    child_pos = text.index("reactor Machine_running {")
    machine_pos = text.index("reactor Machine {")
    main_pos = text.index("main reactor {")
    assert child_pos < machine_pos < main_pos
    assert "m = new Machine()" in text
    assert "    c_running = new Machine_running()\n" in text
    assert "    Tick -> c_running.Tick\n" in text


def test_reactor_scope_instantiations_render() -> None:
    # A parallel root machine instantiates its regions at reactor scope.
    region = Reactor(
        name="M_lights",
        inputs=("Flip",),
        outputs=("completed", "current_state"),
        modes=(Mode(name="off", initial=True, reactions=(_announce("off"),)),),
    )
    machine = Reactor(
        name="M",
        inputs=("Flip",),
        outputs=("current_state",),
        instantiations=(Instantiation("c_lights", "M_lights"),),
        connections=(Connection("Flip", "c_lights.Flip"),),
    )
    text = to_lf(LfProgram(reactors=(region, machine)))
    assert "  c_lights = new M_lights()\n" in text
    assert "  Flip -> c_lights.Flip\n" in text


def test_explicit_main_reactor_and_target() -> None:
    from sysmlc.backends.rosetta.program import MainReactor

    prog = LfProgram(
        reactors=(Reactor(name="Plant"), Reactor(name="Tester")),
        main=MainReactor(
            instantiations=(
                Instantiation("plant", "Plant"),
                Instantiation("tb", "Tester"),
            ),
            connections=(Connection("plant.Pong", "tb.Pong"),),
        ),
        target_options=(("fast", "true"), ("timeout", "5 sec")),
    )
    text = to_lf(prog)
    assert "target Python {" in text
    assert "fast: true" in text and "timeout: 5 sec" in text
    assert "main reactor {" in text
    assert "plant = new Plant()" in text
    assert "plant.Pong -> tb.Pong" in text


def test_bare_target_and_trivial_main_unchanged() -> None:
    # No target_options / no main -> byte-identical legacy output.
    text = to_lf(LfProgram(reactors=(Reactor(name="M"),)))
    assert text.startswith("target Python\n")
    assert "main reactor {\n  m = new M()\n}\n" in text
    assert "target Python {" not in text


def test_preamble_with_types_module_import_renders_correctly() -> None:
    # When a companion module exists, the preamble should contain an import
    # line, not inline class definitions.
    from sysmlc.backends.rosetta.program import LfProgram

    prog = LfProgram(
        reactors=(Reactor(name="Machine"),),
        preamble=("from Machine_types import Point",),
        target_options=(("files", '["Machine_types.py"]'),),
        types_module_name="Machine_types",
        types_module_lines=(
            "from dataclasses import dataclass",
            "@dataclass",
            "class Point:",
            "    x: float = 0.5",
        ),
    )
    text = to_lf(prog)
    assert 'files: ["Machine_types.py"],' in text
    assert "from Machine_types import Point" in text
    assert "@dataclass" not in text  # class not inlined in the .lf
