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
    program = _build("traffic-light", "TrafficLight::TrafficLightBehavior")
    assert "from enum import Enum" in program.preamble
    assert "class LightColor(Enum):" in program.preamble
    assert '    red = "red"' in program.preamble
    assert '    green = "green"' in program.preamble
    assert '    yellow = "yellow"' in program.preamble


def test_traffic_light_enum_literals_in_bodies_and_init() -> None:
    program = _build("traffic-light", "TrafficLight::TrafficLightBehavior")
    (color, _requested) = program.reactor.state_vars
    assert color.name == "color"
    assert color.init == "LightColor.red"
    entry = _mode(program, "showGreen").reactions[0]
    assert "self.color = LightColor.green" in entry.body


def test_traffic_light_structure() -> None:
    program = _build("traffic-light", "TrafficLight::TrafficLightBehavior")
    # walkRequest is a transient latch entered when PedestrianRequest is
    # accepted; its eventless completion transition sends WalkOn via commPort
    # at the next microstep, ensuring the testbench is settled in walkWait
    # before WalkOn arrives (LF modal mode transitions take effect one
    # microstep after the triggering reaction).
    assert [m.name for m in program.reactor.modes] == [
        "showRed",
        "showGreen",
        "walkRequest",
        "showYellow",
    ]
    assert program.reactor.inputs == ("PedestrianRequest",)
    text = to_lf(program)
    # Timer names are state-qualified: lfc flattens mode-locals into one
    # per-reactor struct, so identical names across modes would collide.
    assert "timer t_showRed(4 sec)" in text


# -- stopwatch: periodic after-self-loop --


def test_stopwatch_self_loop_timer() -> None:
    program = _build("stopwatch", "Stopwatch::StopwatchBehavior")
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
    program = _build("stopwatch", "Stopwatch::StopwatchBehavior")
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
    program = _build(
        "vending-machine", "VendingMachine::VendingMachineBehavior"
    )
    idle = _mode(program, "idle")
    # [0] entry; [1] the Coin-triggered reaction
    coin = idle.reactions[1]
    assert coin.triggers == ("Coin",)
    assert coin.body[0] == "coin = Coin.value"
    assert "if self.credit + coin.value < self.price:" in coin.body


def test_vending_dispatch_merges_same_signal_groups() -> None:
    program = _build(
        "vending-machine", "VendingMachine::VendingMachineBehavior"
    )
    idle = _mode(program, "idle")
    # both Coin transitions merge into ONE reaction (if/elif)
    assert len(idle.reactions) == 2  # entry + Coin
    coin = idle.reactions[1]
    branches = sum(line.startswith(("if ", "elif ")) for line in coin.body)
    assert branches == 2


def test_vending_dispense_constructs_payload_class() -> None:
    program = _build(
        "vending-machine", "VendingMachine::VendingMachineBehavior"
    )
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
    program = _build("microwave", "Microwave::MicrowaveBehavior")
    assert [r.name for r in program.reactors] == [
        "MicrowaveBehavior_cooking_heating_heater",
        "MicrowaveBehavior_cooking_heating_turntable",
        "MicrowaveBehavior_cooking",
        "MicrowaveBehavior",
    ]


def test_microwave_forwards_only_inner_signals() -> None:
    # StartCmd/DoorOpen are handled at the root; only the pause pair is
    # accepted inside `cooking` and forwarded down.
    program = _build("microwave", "Microwave::MicrowaveBehavior")
    cooking = _mode(program, "cooking")
    assert [(c.source, c.target) for c in cooking.connections] == [
        ("PauseCmd", "c_cooking.PauseCmd"),
        ("ResumeCmd", "c_cooking.ResumeCmd"),
    ]


def test_microwave_join_flags_track_region_completion() -> None:
    program = _build("microwave", "Microwave::MicrowaveBehavior")
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


# -- flagship case studies: milling workcell and batch reactor --


def test_milling_workcell_builds_nested_reactor_family() -> None:
    program = _build(
        "milling-workcell", "MillingWorkcell::MillingWorkcellBehavior"
    )
    assert [r.name for r in program.reactors] == [
        "MillingWorkcellBehavior_homing",
        "MillingWorkcellBehavior_producing_machining_spindle",
        "MillingWorkcellBehavior_producing_machining_coolant",
        "MillingWorkcellBehavior_producing_machining_monitor",
        "MillingWorkcellBehavior_producing",
        "MillingWorkcellBehavior",
    ]
    assert [p.name for p in program.reactor.parameters] == [
        "batchSize",
        "toolWearLimit",
        "wearPerPiece",
        "maxRetries",
    ]


def test_milling_workcell_deep_exit_propagates_two_scopes() -> None:
    # monitor.tripped exits to the ROOT's faultRecovery: the region raises
    # exit_0, the producing reactor re-raises it, the root resolves it.
    program = _build(
        "milling-workcell", "MillingWorkcell::MillingWorkcellBehavior"
    )
    monitor = program.reactors[3]
    assert "exit_0" in monitor.outputs
    producing = program.reactors[4]
    assert "exit_0" in producing.outputs
    (machining,) = [m for m in producing.modes if m.name == "machining"]
    (reraise,) = [
        r for r in machining.reactions if r.triggers == ("c_monitor.exit_0",)
    ]
    assert reraise.effects == ("exit_0",)
    root_producing = _mode(program, "producing")
    (resolve,) = [
        r
        for r in root_producing.reactions
        if r.triggers == ("c_producing.exit_0",)
    ]
    assert resolve.effects == ("reset(faultRecovery)",)


def test_milling_workcell_batch_loop_dispatches_on_completion() -> None:
    # The `announcing` transient latch (added to break the LF causality
    # cycle in rig mode) moved the BatchReport send out of the
    # c_producing.completed reaction and into announcing's startup reaction.
    # The completion reaction now transitions to either `producing` (loop)
    # or `announcing` (batch done); BatchReport is sent from announcing.
    program = _build(
        "milling-workcell", "MillingWorkcell::MillingWorkcellBehavior"
    )
    producing = _mode(program, "producing")
    (completion,) = [
        r
        for r in producing.reactions
        if r.triggers == ("c_producing.completed",)
    ]
    body = "\n".join(completion.body)
    assert "if self.produced + 1 < self.batchSize:" in body
    assert "announcing.set()" in body
    assert "SysML constraint wearWithinLimit violated" in body
    # BatchReport is sent from the `announcing` mode startup reaction,
    # not from the c_producing.completed reaction.
    announcing = _mode(program, "announcing")
    (startup,) = [r for r in announcing.reactions if "startup" in r.triggers]
    # In standalone mode the send renders as a logical-action schedule;
    # in rig mode it becomes BatchReport.set() on the output port.
    # Either way the announcement payload is present in the body.
    assert "BatchReport" in "\n".join(startup.body)


def test_batch_reactor_parallel_regions_and_payload_guard() -> None:
    program = _build("batch-reactor", "BatchReactor::BatchReactorBehavior")
    assert [r.name for r in program.reactors] == [
        "BatchReactorBehavior_reacting_agitation",
        "BatchReactorBehavior_reacting_ventWatch",
        "BatchReactorBehavior",
    ]
    vent_watch = program.reactors[1]
    (watching,) = [m for m in vent_watch.modes if m.name == "watching"]
    (guard_reaction,) = [
        r for r in watching.reactions if "PressureReading" in r.triggers
    ]
    assert guard_reaction.body[0] == "reading = PressureReading.value"
    assert guard_reaction.body[1] == "if reading.bar >= 9.0:"


def test_batch_reactor_saturating_dynamics_use_whitelist() -> None:
    program = _build("batch-reactor", "BatchReactor::BatchReactorBehavior")
    filling = _mode(program, "filling")
    timer_reaction = filling.reactions[1]
    assert (
        "self.level = min(self.level + self.inflowRate, self.tankCapacity)"
        in timer_reaction.body
    )


def test_showcase_values_examples_configure_and_build() -> None:
    # Every shipped values.yaml must configure its model and build.
    import pytest

    from sysmlc.values import configure_model, load_values, select_values

    examples = [
        ("thermostat", "Thermostat::Thermostat"),
        ("milling-workcell", "MillingWorkcell::MillingWorkcellBehavior"),
        ("batch-reactor", "BatchReactor::BatchReactorBehavior"),
        ("charging-station", "ChargingStation::ChargingStationBehavior"),
        ("level-crossing", "LevelCrossing::LevelCrossingBehavior"),
    ]
    for example, qn in examples:
        values_file = SHOWCASE_DIR / example / "values.yaml"
        if not values_file.exists():
            pytest.fail(f"missing values.yaml for {example}")
        model = load_model(SHOWCASE_DIR / example)
        overrides = select_values(load_values(values_file), qn)
        assert overrides, f"values.yaml for {example} selects nothing"
        program = build_program(configure_model(model, qn, overrides), qn)
        assert program.reactor.name == qn.split("::")[-1]


# -- charging station and level crossing --


def test_charging_station_priority_guards_derate_first() -> None:
    # Declaration order is firing priority: in `bulk`, thermal derating
    # preempts the energy threshold.
    program = _build(
        "charging-station", "ChargingStation::ChargingStationBehavior"
    )
    assert [r.name for r in program.reactors] == [
        "ChargingStationBehavior_handshake",
        "ChargingStationBehavior",
    ]
    bulk = _mode(program, "bulk")
    entry = bulk.reactions[0]
    body = list(entry.body)
    cooling_at = body.index("if self.temperature >= self.tempLimit:")
    topoff_at = body.index(
        "elif self.sessionEnergy >= self.targetEnergy * 0.9:"
    )
    assert cooling_at < topoff_at


def test_charging_station_auth_dispatch_and_timeout() -> None:
    program = _build(
        "charging-station", "ChargingStation::ChargingStationBehavior"
    )
    authorizing = _mode(program, "authorizing")
    (auth,) = [r for r in authorizing.reactions if "AuthResult" in r.triggers]
    assert auth.body[0] == "r = AuthResult.value"
    assert auth.body[1] == "if r.code == 1:"
    # The timeout is an attribute-duration logical action scheduled on
    # entry.
    (action,) = authorizing.actions
    assert action.name == "after_authorizing_act"


def test_level_crossing_reactor_family_and_fault_paths() -> None:
    program = _build("level-crossing", "LevelCrossing::LevelCrossingBehavior")
    assert [r.name for r in program.reactors] == [
        "LevelCrossingBehavior_securing",
        "LevelCrossingBehavior_closed_bellCycle",
        "LevelCrossingBehavior_closed_passage",
        "LevelCrossingBehavior_opening",
        "LevelCrossingBehavior",
    ]
    securing = program.reactors[0]
    assert "exit_0" in securing.outputs
    root_securing = _mode(program, "securing")
    (resolve,) = [
        r
        for r in root_securing.reactions
        if r.triggers == ("c_securing.exit_0",)
    ]
    assert resolve.effects == ("reset(failSafe)",)


def test_level_crossing_join_mixes_timer_and_signal_regions() -> None:
    # The closed state's join waits for a time-driven region (bellCycle)
    # AND a signal-driven one (passage).
    program = _build("level-crossing", "LevelCrossing::LevelCrossingBehavior")
    passage = program.reactors[2]
    (waiting,) = [m for m in passage.modes if m.name == "waiting"]
    (passed,) = [r for r in waiting.reactions if "TrainPassed" in r.triggers]
    assert passed.effects == ("reset(done)",)
    closed = _mode(program, "closed")
    (join,) = [
        r
        for r in closed.reactions
        if r.triggers == ("c_bellCycle.completed", "c_passage.completed")
    ]
    assert "if self.closed_bellCycle_done and self.closed_passage_done:" in (
        join.body
    )
