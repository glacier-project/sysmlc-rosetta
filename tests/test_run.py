"""Compile generated LF programs with lfc and execute them.

Each test renders the machine with the rosetta backend, writes a harness
that imports the machine reactor (the machine file's own ``main reactor``
is ignored on import -- verified against lfc 0.11), drives scripted
inputs, and asserts the sequence of ``current_state`` announcements
printed by the harness. ``fast: true`` makes logical time run
unthrottled, so ``after`` delays don't cost wall-clock time.

Each test compiles with lfc (seconds per test); run with ``-m "not lf"``
for a fast local loop when lfc is installed.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from sysmlc.backends.rosetta.builder import OUTPUT_PORT, build_program
from sysmlc.backends.rosetta.composition import build_rig_program
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.sysml.loading import load_model
from sysmlc.values import configure_model
from tests.backends.rosetta.conftest import FIXTURES_DIR
from tests.backends.showcase import SHOWCASE_DIR
from tests.backends.sm_examples import SM_EXAMPLES_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from sysmlc.values import ValueNode

pytestmark = [
    pytest.mark.lf,
    pytest.mark.skipif(
        shutil.which("lfc") is None, reason="lfc is not on PATH"
    ),
]

HARNESS = """target Python {{
  fast: true,
  timeout: {timeout}
}}

import {reactor} from "{machine}.lf"

main reactor {{
  m = new {reactor}()
{drivers}
  reaction(m.current_state) {{=
    print(f"STATE: {{m.current_state.value}}")
  =}}
}}
"""


def compile_harness(
    tmp_path: Path,
    model_dir: Path,
    qn: str,
    *,
    timeout: str = "2 sec",
    drivers: str = "",
    values: dict[str, ValueNode] | None = None,
) -> Path:
    """Generate and lfc-compile one machine; return the harness binary."""
    name = qn.split("::")[-1]
    model = load_model(model_dir)
    if values:
        model = configure_model(model, qn, values)
    src = tmp_path / "src"
    src.mkdir()
    (src / f"{name}.lf").write_text(to_lf(build_program(model, qn)))
    (src / "Harness.lf").write_text(
        HARNESS.format(
            reactor=name, machine=name, timeout=timeout, drivers=drivers
        )
    )
    compile_result = subprocess.run(
        ["lfc", str(src / "Harness.lf")],
        capture_output=True,
        timeout=600,
    )
    assert compile_result.returncode == 0, compile_result.stderr.decode(
        errors="replace"
    )
    return tmp_path / "bin" / "Harness"


def run_machine(
    tmp_path: Path,
    model_dir: Path,
    qn: str,
    *,
    timeout: str = "2 sec",
    drivers: str = "",
    values: dict[str, ValueNode] | None = None,
) -> list[str]:
    """Generate, compile, and run one machine; return the state sequence."""
    binary = compile_harness(
        tmp_path,
        model_dir,
        qn,
        timeout=timeout,
        drivers=drivers,
        values=values,
    )
    result = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return [
        line.removeprefix("STATE: ")
        for line in result.stdout.splitlines()
        if line.startswith("STATE: ")
    ]


RIG_HARNESS = """target Python {{
  fast: true,
  timeout: {timeout}
}}

import {reactor} from "{machine}.lf"

main reactor {{
  m = new {reactor}()
{observers}
}}
"""

OBSERVER = """  reaction(m.{port}) {{=
    print(f"STATE {label}: {{m.{port}.value}}")
  =}}"""


def run_rig(
    tmp_path: Path,
    model_dir: Path,
    rig_qn: str,
    *,
    timeout: str = "5 sec",
    values: dict[str, dict[str, ValueNode]] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]]]:
    """Build, compile, and run a rig; return (process, states per stream).

    ``values`` maps a machine's qualified name to its overrides.
    """
    name = rig_qn.split("::")[-1]
    model = load_model(model_dir)
    if values:
        for machine_qn, overrides in values.items():
            model = configure_model(model, machine_qn, overrides)
    program = build_rig_program(model, rig_qn)
    streams = [
        port.removesuffix(f"_{OUTPUT_PORT}") for port in program.reactor.outputs
    ]
    src = tmp_path / "src"
    src.mkdir()
    (src / f"{name}.lf").write_text(to_lf(program))
    observers = "\n".join(
        OBSERVER.format(port=f"{label}_current_state", label=label)
        for label in streams
    )
    (src / "Harness.lf").write_text(
        RIG_HARNESS.format(
            reactor=name,
            machine=name,
            timeout=timeout,
            observers=observers,
        )
    )
    compile_result = subprocess.run(
        ["lfc", str(src / "Harness.lf")], capture_output=True, timeout=600
    )
    assert compile_result.returncode == 0, compile_result.stderr.decode(
        errors="replace"
    )
    process = subprocess.run(
        [str(tmp_path / "bin" / "Harness")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    states: dict[str, list[str]] = {label: [] for label in streams}
    for line in process.stdout.splitlines():
        for label in streams:
            prefix = f"STATE {label}: "
            if line.startswith(prefix):
                states[label].append(line.removeprefix(prefix))
    return process, states


def test_sm01_eventless_chain(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path, SM_EXAMPLES_DIR / "sm01-helloworld", "SM01::Machine"
    )
    assert states == ["idle", "running"]


def test_sm02_signal_trigger(tmp_path: Path) -> None:
    drivers = (
        "  timer tick(100 msec)\n"
        "  reaction(tick) -> m.Tick {=\n"
        "    m.Tick.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm02-event-trigger",
        "SM02::MachinePortless",
        drivers=drivers,
    )
    assert states == ["idle", "running"]


def test_sm10_done_terminates(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path, SM_EXAMPLES_DIR / "sm10-done", "SM10::MachineRootDone"
    )
    assert states == ["idle", "running", "done"]


def test_sm11_self_send(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path, SM_EXAMPLES_DIR / "sm11-send-effect", "SM11::MachineSelfSend"
    )
    assert states == ["idle", "armed", "fired"]


def test_sm12_do_send(tmp_path: Path) -> None:
    # The do-action send is scheduled from the entry reaction; this runs
    # only if the scheduled action is declared in the effects clause.
    states = run_machine(
        tmp_path, SM_EXAMPLES_DIR / "sm12-do-action", "SM12::MachineDoSend"
    )
    assert states == ["working", "finished"]


def test_sm13_literal_after(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm13-time-trigger",
        "SM13::MachineAfterSeconds",
        timeout="10 sec",
    )
    assert states == ["idle", "running", "done"]


def test_sm13_attribute_after(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm13-time-trigger",
        "SM13::MachineAfterAttribute",
        timeout="200 sec",  # pickDuration default is 2 min of logical time
    )
    assert states == ["idle", "running", "done"]


def test_after_with_guard(tmp_path: Path) -> None:
    # `accept after` + `if` guard: the capability quake must reject; in LF
    # the timer fires once and the guard is evaluated at that instant.
    states = run_machine(
        tmp_path,
        FIXTURES_DIR / "after-guard",
        "AfterGuard::Machine",
        timeout="10 sec",
    )
    assert states == ["idle", "running", "done"]


def test_sm05_composite_state_var(tmp_path: Path) -> None:
    # SimpleNamespace initializers must serialize as {= ... =} target code.
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm05-chained-references",
        "SM05::MachineChainGuard",
    )
    assert states == ["idle", "running"]


def test_showcase_traffic_light(tmp_path: Path) -> None:
    drivers = (
        "  timer ped(5 sec)\n"
        "  reaction(ped) -> m.PedestrianRequest {=\n"
        "    m.PedestrianRequest.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "traffic-light",
        "TrafficLight::TrafficLight",
        timeout="6 sec",
        drivers=drivers,
    )
    # walkRequest is a transient latch that announces immediately before
    # transitioning to showYellow; it appears when PedestrianRequest is
    # accepted (see traffic_light.sysml for the LF modal microstep rationale).
    assert states == ["showRed", "showGreen", "walkRequest", "showYellow"]


def test_traffic_light_rig_verdict(tmp_path: Path) -> None:
    # TrafficLightRig: testbench waits 5 s, then sends PedestrianRequest from
    # the walkWait entry action (one microstep after the mode is active, so
    # reaction(WalkOn) fires in walkWait, not waitGreen). Plant enters the
    # transient walkRequest state, announces WalkOn, then showYellow. Verdict
    # passes (exit 0).
    process, states = run_rig(
        tmp_path,
        SHOWCASE_DIR / "traffic-light",
        "TrafficLight::TrafficLightRig",
        timeout="20 sec",
    )
    assert process.returncode == 0, process.stderr
    # walkRequest is the unique landmark: it is only entered when a
    # PedestrianRequest is accepted while showGreen is active.
    assert "walkRequest" in states["plant"]
    assert states["plant"][-1] == "showYellow"
    assert states["tb"][-1] == "done"


def test_showcase_stopwatch(tmp_path: Path) -> None:
    drivers = (
        "  timer go(100 msec)\n"
        "  reaction(go) -> m.StartCmd {=\n"
        "    m.StartCmd.set(True)\n"
        "  =}\n"
        "  timer halt(3500 msec)\n"
        "  reaction(halt) -> m.StopCmd {=\n"
        "    m.StopCmd.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "stopwatch",
        "Stopwatch::Stopwatch",
        timeout="4 sec",
        drivers=drivers,
    )
    # stopped, running, then one announcement per periodic re-entry
    # (ticks at 1.1 s, 2.1 s, 3.1 s), then stopped at 3.5 s.
    assert states[0] == "stopped"
    assert states[-1] == "stopped"
    assert states.count("running") == 4  # initial entry + 3 re-entries


def test_stopwatch_rig_verdict(tmp_path: Path) -> None:
    # StopwatchRig: testbench sends StartCmd at 0.1 s, waits 3.4 s (absolute
    # 3.5 s), then enters stopWait whose entry action sends StopCmd -- so the
    # mode is already active when Stopped comes back. Plant ticks 3 times while
    # running (at 1.1 s, 2.1 s, 3.1 s), then stops. Verdict passes (exit 0).
    process, states = run_rig(
        tmp_path,
        SHOWCASE_DIR / "stopwatch",
        "Stopwatch::StopwatchRig",
        timeout="15 sec",
    )
    assert process.returncode == 0, process.stderr
    # 4 running entries is the unique landmark: initial entry + 3 periodic
    # re-entries proving the stopwatch ticked while running before stopping.
    assert states["plant"].count("running") == 4
    assert states["plant"][-1] == "stopped"
    assert states["tb"][-1] == "done"


def test_showcase_thermostat(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "thermostat",
        "Thermostat::Thermostat",
        timeout="20 sec",
    )
    # 18.0 heats by 0.8/s past 21.5 (5 ticks), idles down by 0.3/s to
    # 20.5, then heats again: the loop must alternate at least once.
    assert states[0] == "heating"
    assert "idle" in states
    idx = states.index("idle")
    assert "heating" in states[idx:]


def test_showcase_thermostat_override_changes_behavior(
    tmp_path: Path,
) -> None:
    # setpoint 23 + hysteresis 1: heating needs 8 ticks to reach 24.0
    # (5 ticks by default) — the values override visibly changes behavior.
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "thermostat",
        "Thermostat::Thermostat",
        timeout="12 sec",
        values={"setpoint": 23.0, "hysteresis": 1.0},
    )
    first_idle = states.index("idle")
    assert states[:first_idle].count("heating") == 9


def test_showcase_thermostat_violated_constraint_aborts(
    tmp_path: Path,
) -> None:
    # The negative testbench: a violating override trips the startup
    # check, so the run aborts with a nonzero exit naming the constraint.
    binary = compile_harness(
        tmp_path,
        SHOWCASE_DIR / "thermostat",
        "Thermostat::Thermostat",
        values={"setpoint": -10.0},
    )
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode != 0
    assert "setpointPositive" in result.stderr


def test_showcase_vending_machine(tmp_path: Path) -> None:
    # Payloads are duck-typed: the harness cannot import the machine
    # file's preamble classes, so SimpleNamespace stands in for Coin and
    # Selection.
    # A reactor-level preamble block places its import inside __init__, not
    # at module scope, so reaction functions cannot see it.  lfc also rejects
    # single-quoted strings inside {= =} blocks; use sys.modules["types"] via
    # the module-level sys that lfc's Python target always imports.
    drivers = (
        "  timer c1(100 msec)\n"
        "  reaction(c1) -> m.Coin {=\n"
        '    m.Coin.set(sys.modules["types"].SimpleNamespace(value=2))\n'
        "  =}\n"
        "  timer c2(200 msec)\n"
        "  reaction(c2) -> m.Coin {=\n"
        '    m.Coin.set(sys.modules["types"].SimpleNamespace(value=2))\n'
        "  =}\n"
        "  timer s1(300 msec)\n"
        "  reaction(s1) -> m.Selection {=\n"
        "    m.Selection.set("
        'sys.modules["types"].SimpleNamespace(product="cola"))\n'
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "vending-machine",
        "VendingMachine::VendingMachine",
        timeout="1 sec",
        drivers=drivers,
    )
    # 2 < 3 keeps idle (self-loop re-entry), 4 >= 3 reaches paid,
    # selection dispenses back to idle.
    assert states == ["idle", "idle", "paid", "idle"]


def test_vending_machine_rig_verdict(tmp_path: Path) -> None:
    # VendingMachineRig: testbench inserts two Coin(2) payments at 0.1 s
    # intervals (credit 2 then 4 >= price 3, plant → paid), then sends
    # Selection("cola") from dispenseWait's entry action so the mode is
    # already active when the plant's Dispensed reply arrives.  Verdict
    # passes (exit 0).
    process, states = run_rig(
        tmp_path,
        SHOWCASE_DIR / "vending-machine",
        "VendingMachine::VendingMachineRig",
        timeout="15 sec",
    )
    assert process.returncode == 0, process.stderr
    # paid is the unique landmark: only entered when cumulative credit
    # meets or exceeds price (after the second Coin(2) is accepted).
    assert "paid" in states["plant"]
    assert states["plant"][-1] == "idle"
    assert states["tb"][-1] == "done"


def test_sm08_nested_composite_runs(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm08-nested-composite",
        "SM08::MachineNested",
        timeout="1 sec",
    )
    assert states == ["idle", "running.warming", "running.hot"]


def test_sm08_deep_exit_runs(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm08-nested-composite",
        "SM08::MachineCrossOut",
        timeout="1 sec",
    )
    assert states == ["idle", "running.warming", "running.hot", "stopped"]


def test_sm09_parallel_root_runs(tmp_path: Path) -> None:
    # Both regions announce at the same tags; within a tag the LAST
    # declared region's announcement wins (deterministic overwrite), so
    # only `sound` is visible while the regions advance in lock-step.
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm09-parallel",
        "SM09::MachineParallel",
        timeout="1 sec",
    )
    assert states == ["sound.silent", "sound.beeping"]


def test_sm09_nested_parallel_runs(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm09-parallel",
        "SM09::MachineNestedParallel",
        timeout="1 sec",
    )
    assert states == ["idle", "dual.sound.silent", "dual.sound.beeping"]


def test_showcase_microwave_completes(tmp_path: Path) -> None:
    # Heater finishes at 0.4 s, turntable at 0.6 s; the join completes
    # `cooking`, whose completion transition returns to idle.
    drivers = (
        "  timer go(100 msec)\n"
        "  reaction(go) -> m.StartCmd {=\n"
        "    m.StartCmd.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "microwave",
        "Microwave::Microwave",
        timeout="2 sec",
        drivers=drivers,
    )
    assert states == [
        "idle",
        "cooking.heating.turntable.rotating",
        "cooking.heating.heater.done",
        "cooking.heating.turntable.done",
        "cooking.done",
        "idle",
    ]


def test_showcase_microwave_pause_and_door_interrupt(tmp_path: Path) -> None:
    # Resume RESETS the parallel regions (composite re-entry restarts the
    # heater's 400 ms), and the door interrupt aborts cooking before the
    # restarted turntable's 600 ms elapse.
    drivers = (
        "  timer go(100 msec)\n"
        "  reaction(go) -> m.StartCmd {=\n"
        "    m.StartCmd.set(True)\n"
        "  =}\n"
        "  timer pause(300 msec)\n"
        "  reaction(pause) -> m.PauseCmd {=\n"
        "    m.PauseCmd.set(True)\n"
        "  =}\n"
        "  timer resume(600 msec)\n"
        "  reaction(resume) -> m.ResumeCmd {=\n"
        "    m.ResumeCmd.set(True)\n"
        "  =}\n"
        "  timer door(1100 msec)\n"
        "  reaction(door) -> m.DoorOpen {=\n"
        "    m.DoorOpen.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "microwave",
        "Microwave::Microwave",
        timeout="2 sec",
        drivers=drivers,
    )
    assert states == [
        "idle",
        "cooking.heating.turntable.rotating",
        "cooking.paused",
        "cooking.heating.turntable.rotating",
        "cooking.heating.heater.done",
        "idle",
    ]


def test_microwave_rig_verdict(tmp_path: Path) -> None:
    # MicrowaveRig: testbench sends StartCmd at 0.1 s, waits for Finished
    # (announced when `cooking` completion fires after ~0.6 s), verdict passes.
    process, states = run_rig(
        tmp_path,
        SHOWCASE_DIR / "microwave",
        "Microwave::MicrowaveRig",
        timeout="10 sec",
    )
    assert process.returncode == 0, process.stderr
    # cooking.heating.turntable.rotating is the deepest-path announcement at
    # the composite entry tag (heater and turntable both enter, but turntable
    # is declared last so its announcement overwrites heater's at the same tag).
    assert "cooking.heating.turntable.rotating" in states["plant"]
    assert states["plant"][-1] == "idle"
    assert states["tb"][-1] == "done"


def test_showcase_furuta_pendulum(tmp_path: Path) -> None:
    # Decreasing |theta| swings up, catches, then stabilizes; a late spike
    # past dropAngle knocks it back to swing-up. SimpleNamespace stands in
    # for AngleReading (payloads are duck-typed across files).
    #
    # `announcing` is a transient latch entered when catching confirms the
    # second sub-catchAngle reading; its completion transition sends Balanced
    # via commPort and enters stabilizing (same pattern as traffic-light's
    # `walkRequest`). It appears as a visible state announcement here because
    # lfc emits a current_state announcement on every mode entry, even
    # transient ones.
    drivers = (
        "  state thetas = {= [1.5, 0.9, 0.4, 0.3, 0.2, 0.1, 1.4] =}\n"
        "  state i = 0\n"
        "  timer sample(100 msec, 100 msec)\n"
        "  reaction(sample) -> m.AngleReading {=\n"
        "    if self.i < len(self.thetas):\n"
        "      m.AngleReading.set(\n"
        '        sys.modules["types"]'
        ".SimpleNamespace(theta=self.thetas[self.i])\n"
        "      )\n"
        "      self.i += 1\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "furuta-pendulum",
        "FurutaPendulum::PendulumController",
        timeout="1 sec",
        drivers=drivers,
    )
    assert states == [
        "swingUp",
        "catching",
        "announcing",
        "stabilizing",
        "swingUp",
    ]


def test_furuta_rig_verdict(tmp_path: Path) -> None:
    # FurutaRig: testbench sends four AngleReading values that drive
    # swingUp→catching (theta=0.4) then catching→announcing→stabilizing
    # (theta=0.3). The `announcing` transient state sends Balanced via
    # commPort one microstep after step4's timer fires, while balancedWait
    # is already the active testbench state -- so the accept fires cleanly.
    # Verdict passes (exit 0).
    process, states = run_rig(
        tmp_path,
        SHOWCASE_DIR / "furuta-pendulum",
        "FurutaPendulum::FurutaRig",
        timeout="10 sec",
    )
    assert process.returncode == 0, process.stderr
    # `announcing` is the unique plant landmark: it is only entered when
    # catching receives a second consecutive sub-catchAngle reading, i.e.
    # the controller has confirmed the pendulum is in the balanced region.
    assert "announcing" in states["plant"]
    assert states["plant"][-1] == "stabilizing"
    assert states["tb"][-1] == "done"


_WORKCELL_PIECE = [
    "producing.loadPart",
    "producing.machining.monitor.watching",
    "producing.machining.coolant.flowing",
    "producing.machining.spindle.cutting",
    "producing.machining.coolant.done",
    "producing.machining.spindle.done",
    "producing.machining.monitor.done",
    "producing.unloadPart",
    "producing.done",
]

_WORKCELL_POWER_UP = [
    "cold",
    "homing.axisX",
    "homing.axisY",
    "homing.axisZ",
    "homing.done",
    "ready",
]


def test_showcase_milling_workcell_batch(tmp_path: Path) -> None:
    # Power-up homing, a 2-piece batch (values override), shutdown: every
    # piece runs load -> parallel machining (join) -> unload -> completion.
    drivers = (
        "  timer power(100 msec)\n"
        "  reaction(power) -> m.PowerOn {=\n"
        "    m.PowerOn.set(True)\n"
        "  =}\n"
        "  timer start(1200 msec)\n"
        "  reaction(start) -> m.StartBatch {=\n"
        "    m.StartBatch.set(True)\n"
        "  =}\n"
        "  timer off(7 sec)\n"
        "  reaction(off) -> m.Shutdown {=\n"
        "    m.Shutdown.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "milling-workcell",
        "MillingWorkcell::MillingWorkcell",
        timeout="10 sec",
        drivers=drivers,
        values={"batchSize": 2},
    )
    # `announcing` is the transient latch that sends BatchReport one
    # microstep after the final piece completes; it appears in the state
    # sequence before `ready` because lfc emits a current_state
    # announcement on every mode entry, even transient ones.
    assert states == [
        *_WORKCELL_POWER_UP,
        *_WORKCELL_PIECE,
        *_WORKCELL_PIECE,
        "announcing",
        "ready",
        "done",
    ]


def test_showcase_milling_workcell_fault_retry(tmp_path: Path) -> None:
    # A strong vibration reading trips the monitor mid-cut: the region
    # deep-exits TWO scopes into faultRecovery, triage retries, and the
    # piece completes on the second attempt.
    drivers = (
        "  timer power(100 msec)\n"
        "  reaction(power) -> m.PowerOn {=\n"
        "    m.PowerOn.set(True)\n"
        "  =}\n"
        "  timer start(1200 msec)\n"
        "  reaction(start) -> m.StartBatch {=\n"
        "    m.StartBatch.set(True)\n"
        "  =}\n"
        "  timer spike(2200 msec)\n"
        "  reaction(spike) -> m.VibrationSpike {=\n"
        '    m.VibrationSpike.set(sys.modules["types"]'
        ".SimpleNamespace(level=5))\n"
        "  =}\n"
        "  timer off(8 sec)\n"
        "  reaction(off) -> m.Shutdown {=\n"
        "    m.Shutdown.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "milling-workcell",
        "MillingWorkcell::MillingWorkcell",
        timeout="10 sec",
        drivers=drivers,
        values={"batchSize": 1},
    )
    # `announcing` appears after the retried piece completes: the
    # transient latch sends BatchReport one microstep later (same
    # batch-completion path as the normal run).
    assert states == [
        *_WORKCELL_POWER_UP,
        "producing.loadPart",
        "producing.machining.monitor.watching",
        "producing.machining.coolant.flowing",
        "producing.machining.spindle.cutting",
        "producing.machining.monitor.tripped",
        "faultRecovery",
        "triage",
        *_WORKCELL_PIECE,
        "announcing",
        "ready",
        "done",
    ]


def test_milling_workcell_rig_verdict(tmp_path: Path) -> None:
    # MillingWorkcellRig: testbench sends PowerOn at 0.1 s, StartBatch at
    # 1.2 s (abs), then waits for BatchReport from the plant's `announcing`
    # transient latch. The latch fires one microstep after the final piece
    # completes, so batchWait is already active when BatchReport arrives.
    # On receipt the testbench enters done (request_stop ends the run).
    #
    # The testbench intentionally omits Shutdown to avoid an lfc 0.11
    # causality cycle: wiring tb.Shutdown → plant.Shutdown creates a
    # same-tag path from announcing.startup through tb.batchWait → Shutdown
    # → plant.ready reaction(Shutdown). The plant stays in `ready` while
    # the testbench's done calls request_stop(). Verdict passes (exit 0).
    #
    # values override: batchSize=1 so exactly one piece is machined before
    # BatchReport; the rig ships values.yaml with batchSize=2 for run_all.
    process, states = run_rig(
        tmp_path,
        SHOWCASE_DIR / "milling-workcell",
        "MillingWorkcell::MillingWorkcellRig",
        timeout="30 sec",
        values={"MillingWorkcell::MillingWorkcell": {"batchSize": 1}},
    )
    assert process.returncode == 0, process.stderr
    # `announcing` is the unique plant landmark: it is only entered when
    # produced+1 >= batchSize on a piece completion, proving a full batch
    # was machined and BatchReport was sent to the testbench.
    assert "announcing" in states["plant"]
    assert states["plant"][-1] == "ready"
    assert states["tb"][-1] == "done"


_REACTOR_VALUES = {
    "batchTarget": 1,
    "inflowRate": 60.0,
    "heatRate": 65.0,
    "coolRate": 60.0,
    "drainRate": 60.0,
}


def test_showcase_batch_reactor_recipe(tmp_path: Path) -> None:
    # One sped-up batch: closed-loop fill/heat, the parallel reaction
    # stage joining agitation and the watchdog window, cool, drain, stop.
    drivers = (
        "  timer go(200 msec)\n"
        "  reaction(go) -> m.StartRecipe {=\n"
        "    m.StartRecipe.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "batch-reactor",
        "BatchReactor::BatchReactor",
        timeout="20 sec",
        drivers=drivers,
        values=_REACTOR_VALUES,
    )
    assert states == [
        "idle",
        "filling",
        "filling",
        "heating",
        "heating",
        "reacting.ventWatch.watching",
        "reacting.agitation.settling",
        "reacting.agitation.done",
        "reacting.ventWatch.done",
        "cooling",
        "cooling",
        "draining",
        "draining",
        "done",
    ]


def test_showcase_batch_reactor_overpressure(tmp_path: Path) -> None:
    # A 9.5 bar reading trips the watchdog: deep exit into emergency
    # venting, then the recipe degrades gracefully to cooling/draining.
    drivers = (
        "  timer go(200 msec)\n"
        "  reaction(go) -> m.StartRecipe {=\n"
        "    m.StartRecipe.set(True)\n"
        "  =}\n"
        "  timer surge(3 sec)\n"
        "  reaction(surge) -> m.PressureReading {=\n"
        '    m.PressureReading.set(sys.modules["types"]'
        ".SimpleNamespace(bar=9.5))\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "batch-reactor",
        "BatchReactor::BatchReactor",
        timeout="20 sec",
        drivers=drivers,
        values=_REACTOR_VALUES,
    )
    assert states == [
        "idle",
        "filling",
        "filling",
        "heating",
        "heating",
        "reacting.ventWatch.watching",
        "reacting.ventWatch.overpressure",
        "venting",
        "cooling",
        "cooling",
        "draining",
        "draining",
        "done",
    ]


_STATION_HANDSHAKE = [
    "idle",
    "handshake.checkCable",
    "handshake.lockConnector",
    "handshake.done",
    "authorizing",
]


def test_showcase_charging_station_session(tmp_path: Path) -> None:
    # Denied then granted authorization, a three-phase charge with a
    # thermal-derating detour (priority guards), early finish, idle.
    drivers = (
        "  timer plug(200 msec)\n"
        "  reaction(plug) -> m.PlugIn {=\n"
        "    m.PlugIn.set(True)\n"
        "  =}\n"
        "  timer deny(1200 msec)\n"
        "  reaction(deny) -> m.AuthResult {=\n"
        '    m.AuthResult.set(sys.modules["types"]'
        ".SimpleNamespace(code=7))\n"
        "  =}\n"
        "  timer grant(1600 msec)\n"
        "  reaction(grant) -> m.AuthResult {=\n"
        '    m.AuthResult.set(sys.modules["types"]'
        ".SimpleNamespace(code=1))\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "charging-station",
        "ChargingStation::ChargingStation",
        timeout="12 sec",
        drivers=drivers,
        values={"tempLimit": 45.0, "coolThreshold": 40.0},
    )
    assert states == [
        *_STATION_HANDSHAKE,
        "authRetry",
        "authorizing",
        "rampUp",
        "rampUp",
        "rampUp",
        "bulk",
        "bulk",
        "bulk",
        "cooling",
        "cooling",
        "bulk",
        "topOff",
        "finishing",
        "idle",
    ]


def test_showcase_charging_station_auth_exhaustion(tmp_path: Path) -> None:
    # A denial, a timeout, and a second denial burn the retry budget; the
    # station latches `faulted` until an operator reset.
    drivers = (
        "  timer plug(200 msec)\n"
        "  reaction(plug) -> m.PlugIn {=\n"
        "    m.PlugIn.set(True)\n"
        "  =}\n"
        "  timer deny1(1200 msec)\n"
        "  reaction(deny1) -> m.AuthResult {=\n"
        '    m.AuthResult.set(sys.modules["types"]'
        ".SimpleNamespace(code=7))\n"
        "  =}\n"
        "  timer deny2(4 sec)\n"
        "  reaction(deny2) -> m.AuthResult {=\n"
        '    m.AuthResult.set(sys.modules["types"]'
        ".SimpleNamespace(code=9))\n"
        "  =}\n"
        "  timer fix(5 sec)\n"
        "  reaction(fix) -> m.Reset {=\n"
        "    m.Reset.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "charging-station",
        "ChargingStation::ChargingStation",
        timeout="6 sec",
        drivers=drivers,
    )
    assert states == [
        *_STATION_HANDSHAKE,
        "authRetry",
        "authorizing",
        "authRetry",
        "authorizing",
        "authRetry",
        "faulted",
        "idle",
    ]


_CROSSING_SECURING = [
    "securing.warnLights",
    "securing.bell",
    "securing.barrierDown",
]


def test_showcase_level_crossing_passage(tmp_path: Path) -> None:
    # The closed state's join releases the crossing only when the bell
    # cycle has finished AND the train has passed.
    drivers = (
        "  timer approach(300 msec)\n"
        "  reaction(approach) -> m.TrainApproaching {=\n"
        "    m.TrainApproaching.set(True)\n"
        "  =}\n"
        "  timer passed(4 sec)\n"
        "  reaction(passed) -> m.TrainPassed {=\n"
        "    m.TrainPassed.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "level-crossing",
        "LevelCrossing::LevelCrossing",
        timeout="7 sec",
        drivers=drivers,
    )
    assert states == [
        "open",
        *_CROSSING_SECURING,
        "securing.done",
        "closed.passage.waiting",
        "closed.bellCycle.quiet",
        "closed.bellCycle.done",
        "closed.passage.done",
        "opening.barrierUp",
        "opening.lightsOff",
        "opening.done",
        "open",
    ]


def test_showcase_level_crossing_fault_paths(tmp_path: Path) -> None:
    # A severe fault while lowering deep-exits the securing sequence; a
    # later fault while closed is a group interrupt — both latch the
    # fail-safe until reset, and the crossing never reopens on its own.
    drivers = (
        "  timer t1(300 msec)\n"
        "  reaction(t1) -> m.TrainApproaching {=\n"
        "    m.TrainApproaching.set(True)\n"
        "  =}\n"
        "  timer f1(1300 msec)\n"
        "  reaction(f1) -> m.BarrierFault {=\n"
        '    m.BarrierFault.set(sys.modules["types"]'
        ".SimpleNamespace(severity=3))\n"
        "  =}\n"
        "  timer r1(2500 msec)\n"
        "  reaction(r1) -> m.Reset {=\n"
        "    m.Reset.set(True)\n"
        "  =}\n"
        "  timer t2(3 sec)\n"
        "  reaction(t2) -> m.TrainApproaching {=\n"
        "    m.TrainApproaching.set(True)\n"
        "  =}\n"
        "  timer f2(5300 msec)\n"
        "  reaction(f2) -> m.BarrierFault {=\n"
        '    m.BarrierFault.set(sys.modules["types"]'
        ".SimpleNamespace(severity=1))\n"
        "  =}\n"
        "  timer r2(6500 msec)\n"
        "  reaction(r2) -> m.Reset {=\n"
        "    m.Reset.set(True)\n"
        "  =}"
    )
    states = run_machine(
        tmp_path,
        SHOWCASE_DIR / "level-crossing",
        "LevelCrossing::LevelCrossing",
        timeout="8 sec",
        drivers=drivers,
    )
    assert states == [
        "open",
        *_CROSSING_SECURING,
        "failSafe",
        "open",
        *_CROSSING_SECURING,
        "securing.done",
        "closed.passage.waiting",
        "failSafe",
        "open",
    ]


def test_rig_pair_passing_verdict(tmp_path: Path) -> None:
    process, states = run_rig(
        tmp_path, FIXTURES_DIR / "rig-pair", "RigPair::PlantRig"
    )
    assert process.returncode == 0, process.stderr
    # composite entry: the sub-state announcement overwrites "working" at the
    # same tag, so the first visible plant state after idle is "working.grind"
    assert states["plant"][0] == "idle"
    assert "working.grind" in states["plant"]
    assert states["tb"], "tb produced no announcements"
    assert states["tb"][-1] == "done"  # Done observed -> pass


def test_rig_pair_failing_verdict_aborts(tmp_path: Path) -> None:
    process, _states = run_rig(
        tmp_path,
        FIXTURES_DIR / "rig-pair",
        "RigPair::PlantRig",
        values={"RigPair::PlantTest": {"verdict": 1}},
    )
    assert process.returncode != 0
    assert "testPassed" in process.stderr


def test_rig_overlap_serves_local_and_peer(tmp_path: Path) -> None:
    process, states = run_rig(
        tmp_path, FIXTURES_DIR / "rig-overlap", "RigOverlap::PulserRig"
    )
    assert process.returncode == 0, process.stderr
    assert "finished" in states["plant"]  # local self-event delivered
    assert "sawTick" in states["tb"]  # peer port delivered


def test_rig_payload_serves_local_and_peer(tmp_path: Path) -> None:
    # A ported send carrying a payload: the reaction parameter shadows the
    # preamble dataclass, so the constructor must be reached via globals().
    # Without the fix both the set and the schedule line raise TypeError.
    process, states = run_rig(
        tmp_path, FIXTURES_DIR / "rig-payload", "RigPayload::CounterRig"
    )
    assert process.returncode == 0, process.stderr
    assert "finished" in states["plant"]  # local overlap delivery worked
    assert states["tb"][-1] == "done"  # peer payload delivery worked
