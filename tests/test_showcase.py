from __future__ import annotations

from typing import TYPE_CHECKING

from sysmlc.backends.rosetta.builder import build_program
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.sysml.loading import load_model
from tests.backends.showcase import SHOWCASE_DIR

if TYPE_CHECKING:
    from sysmlc.backends.rosetta.program import LfProgram, Mode


def _build(example: str, qn: str) -> LfProgram:
    return build_program(load_model(SHOWCASE_DIR / example), qn)


def _mode(program: LfProgram, name: str) -> Mode:
    (mode,) = [m for m in program.reactor.modes if m.name == name]
    return mode


# -- traffic-light: enumerations --


def test_traffic_light_enum_class_in_preamble() -> None:
    program = _build("traffic-light", "TrafficLight::TrafficLight")
    assert "from enum import Enum" in program.preamble
    assert "class LightColor(Enum):" in program.preamble
    assert '    red = "red"' in program.preamble
    assert '    green = "green"' in program.preamble
    assert '    yellow = "yellow"' in program.preamble


def test_traffic_light_enum_literals_in_bodies_and_init() -> None:
    program = _build("traffic-light", "TrafficLight::TrafficLight")
    (color, _requested) = program.reactor.state_vars
    assert color.name == "color"
    assert color.init == "LightColor.red"
    entry = _mode(program, "showGreen").reactions[0]
    assert "self.color = LightColor.green" in entry.body


def test_traffic_light_structure() -> None:
    program = _build("traffic-light", "TrafficLight::TrafficLight")
    assert [m.name for m in program.reactor.modes] == [
        "showRed",
        "showGreen",
        "showYellow",
    ]
    assert program.reactor.inputs == ("PedestrianRequest",)
    text = to_lf(program)
    # Timer names are state-qualified: lfc flattens mode-locals into one
    # per-reactor struct, so identical names across modes would collide.
    assert "timer t_showRed(4 sec)" in text


# -- stopwatch: periodic after-self-loop --


def test_stopwatch_self_loop_timer() -> None:
    program = _build("stopwatch", "Stopwatch::Stopwatch")
    running = _mode(program, "running")
    (timer,) = running.timers
    assert timer.offset == "1 sec"
    # [0] entry; [1] the timer self-loop reaction
    tick = running.reactions[1]
    assert tick.triggers == (timer.name,)
    assert "self.elapsed = self.elapsed + 1" in tick.body
    assert "running.set()" in tick.body
    assert tick.effects == ("reset(running)",)


def test_stopwatch_signal_self_loop_on_reset_cmd() -> None:
    program = _build("stopwatch", "Stopwatch::Stopwatch")
    stopped = _mode(program, "stopped")
    triggers = [r.triggers for r in stopped.reactions]
    assert ("StartCmd",) in triggers
    assert ("ResetCmd",) in triggers


# -- thermostat: in-attributes become reactor parameters --


def test_thermostat_parameters() -> None:
    program = _build("thermostat", "Thermostat::Thermostat")
    params = {p.name: p.default for p in program.reactor.parameters}
    assert params == {"setpoint": "21.0", "hysteresis": "0.5"}
    (temp,) = program.reactor.state_vars
    assert temp.name == "temperature"
    assert temp.init == "18.0"


def test_thermostat_periodic_loop_with_eventless_guard() -> None:
    program = _build("thermostat", "Thermostat::Thermostat")
    heating = _mode(program, "heating")
    (timer,) = heating.timers
    assert timer.offset == "1 sec"
    entry = heating.reactions[0]
    assert "if self.temperature >= self.setpoint + self.hysteresis:" in (
        entry.body
    )
    assert "    idle.set()" in entry.body
    # [0] entry; [1] the timer self-loop reaction
    tick = heating.reactions[1]
    assert "self.temperature = self.temperature + 0.8" in tick.body


# -- vending-machine: payload reads --


def test_vending_payload_binding_line() -> None:
    program = _build("vending-machine", "VendingMachine::VendingMachine")
    idle = _mode(program, "idle")
    # [0] entry; [1] the Coin-triggered reaction
    coin = idle.reactions[1]
    assert coin.triggers == ("Coin",)
    assert coin.body[0] == "coin = Coin.value"
    assert "if self.credit + coin.value < self.price:" in coin.body


def test_vending_dispatch_merges_same_signal_groups() -> None:
    program = _build("vending-machine", "VendingMachine::VendingMachine")
    idle = _mode(program, "idle")
    # both Coin transitions merge into ONE reaction (if/elif)
    assert len(idle.reactions) == 2  # entry + Coin
    coin = idle.reactions[1]
    branches = sum(line.startswith(("if ", "elif ")) for line in coin.body)
    assert branches == 2


def test_vending_dispense_constructs_payload_class() -> None:
    program = _build("vending-machine", "VendingMachine::VendingMachine")
    assert "@dataclass" in program.preamble
    assert "class Dispensed:" in program.preamble
    paid = _mode(program, "paid")
    sel = paid.reactions[1]
    assert sel.body[0] == "sel = Selection.value"
    assert any(
        "Dispensed_act.schedule(0, Dispensed(product=self.chosen, "
        "change=self.credit))" in line
        for line in sel.body
    )


# -- furuta-pendulum: function calls + payload floats + parameters --


def test_pendulum_whitelisted_calls_and_math_import() -> None:
    program = _build("furuta-pendulum", "FurutaPendulum::PendulumController")
    assert "import math" in program.preamble
    assert not any("class AngleReading" in line for line in program.preamble)
    swing = _mode(program, "swingUp")
    # [0] entry; [1] the AngleReading-triggered reaction
    reading = swing.reactions[1]
    assert reading.body[0] == "reading = AngleReading.value"
    assert "if abs(reading.theta) < self.catchAngle:" in reading.body


def test_pendulum_parameters() -> None:
    program = _build("furuta-pendulum", "FurutaPendulum::PendulumController")
    names = [p.name for p in program.reactor.parameters]
    assert names == ["catchAngle", "dropAngle"]


# -- microwave: hierarchy + parallel (slice 6) --


def test_microwave_builds_nested_reactor_family() -> None:
    program = _build("microwave", "Microwave::Microwave")
    assert [r.name for r in program.reactors] == [
        "Microwave_cooking_heating_heater",
        "Microwave_cooking_heating_turntable",
        "Microwave_cooking",
        "Microwave",
    ]


def test_microwave_forwards_only_inner_signals() -> None:
    # StartCmd/DoorOpen are handled at the root; only the pause pair is
    # accepted inside `cooking` and forwarded down.
    program = _build("microwave", "Microwave::Microwave")
    cooking = _mode(program, "cooking")
    assert [(c.source, c.target) for c in cooking.connections] == [
        ("PauseCmd", "c_cooking.PauseCmd"),
        ("ResumeCmd", "c_cooking.ResumeCmd"),
    ]


def test_microwave_join_flags_track_region_completion() -> None:
    program = _build("microwave", "Microwave::Microwave")
    cooking_reactor = program.reactors[2]
    assert {v.name for v in cooking_reactor.state_vars} == {
        "heating_heater_done",
        "heating_turntable_done",
    }


# -- thermostat: asserted constraints become runtime checks --


def test_thermostat_constraints_check_at_startup() -> None:
    program = _build("thermostat", "Thermostat::Thermostat")
    (startup_checks,) = [
        r for r in program.reactor.reactions if r.triggers == ("startup",)
    ]
    first, second = startup_checks.body
    assert first.startswith(
        "assert self.temperature >= 5.0 and self.temperature <= 40.0"
    )
    assert first.endswith('"SysML constraint tempBand violated"')
    assert second == (
        "assert self.setpoint > 5.0, "
        '"SysML constraint setpointPositive violated"'
    )


def test_thermostat_constraints_follow_assignments() -> None:
    program = _build("thermostat", "Thermostat::Thermostat")
    heating = _mode(program, "heating")
    timer_reaction = heating.reactions[1]
    assert "self.temperature = self.temperature + 0.8" in timer_reaction.body
    assert timer_reaction.body[-1].endswith(
        '"SysML constraint setpointPositive violated"'
    )
    # The entry reaction announces but assigns nothing: no checks there.
    entry = heating.reactions[0]
    assert not any(line.startswith("assert ") for line in entry.body)
