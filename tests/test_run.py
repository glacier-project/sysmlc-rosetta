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

import ast
import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from sysmlc.backends.rosetta.backend import RosettaBackend
from sysmlc.backends.rosetta.builder import OUTPUT_PORT, build_program
from sysmlc.backends.rosetta.parts import build_part_program
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
    python_file: Path | None = None,
) -> Path:
    """Generate and lfc-compile one machine; return the harness binary."""
    name = qn.split("::")[-1]
    model = load_model(model_dir)
    if values:
        model = configure_model(model, qn, values)
    src = tmp_path / "src"
    src.mkdir()
    external = None
    if python_file is not None:
        names = frozenset(
            n.name
            for n in ast.parse(python_file.read_text()).body
            if isinstance(n, ast.FunctionDef)
        )
        external = (python_file.stem, names)
        shutil.copy(python_file, src / python_file.name)
    (src / f"{name}.lf").write_text(
        to_lf(build_program(model, qn, external=external))
    )
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
    python_file: Path | None = None,
) -> list[str]:
    """Generate, compile, and run one machine; return the state sequence."""
    binary = compile_harness(
        tmp_path,
        model_dir,
        qn,
        timeout=timeout,
        drivers=drivers,
        values=values,
        python_file=python_file,
    )
    env = {**os.environ, "PYTHONPATH": str(binary.parent.parent / "src")}
    result = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
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
    program = RosettaBackend().build_composition(model, rig_qn)
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
        "TrafficLight::TrafficLightBehavior",
        timeout="6 sec",
        drivers=drivers,
    )
    # walkRequest is a transient latch that announces immediately before
    # transitioning to showYellow; it appears when PedestrianRequest is
    # accepted (see traffic_light.sysml for the LF modal microstep rationale).
    assert states == ["showRed", "showGreen", "walkRequest", "showYellow"]


def test_traffic_light_rig_verdict(tmp_path: Path) -> None:
    # trafficLightSystem: testbench waits 5 s, then sends PedestrianRequest
    # from the walkWait entry action (one microstep after the mode is active,
    # so reaction(WalkOn) fires in walkWait, not waitGreen). Plant enters the
    # transient walkRequest state, announces WalkOn, then showYellow. Verdict
    # passes (exit 0).
    logs, rc = run_part(
        tmp_path,
        SHOWCASE_DIR / "traffic-light",
        "TrafficLight::trafficLightSystem",
    )
    assert rc == 0, f"expected exit 0 (verdict pass); stderr:\n{logs}"


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
        "Stopwatch::StopwatchBehavior",
        timeout="4 sec",
        drivers=drivers,
    )
    # stopped, running, then one announcement per periodic re-entry
    # (ticks at 1.1 s, 2.1 s, 3.1 s), then stopped at 3.5 s.
    assert states[0] == "stopped"
    assert states[-1] == "stopped"
    assert states.count("running") == 4  # initial entry + 3 re-entries


def test_stopwatch_rig_verdict(tmp_path: Path) -> None:
    # stopwatchSystem: testbench sends StartCmd at 0.1 s, waits 3.4 s
    # (absolute 3.5 s), then enters stopWait whose entry action sends StopCmd
    # -- so the mode is already active when Stopped comes back. Plant ticks 3
    # times while running (at 1.1 s, 2.1 s, 3.1 s), then stops. Verdict
    # passes (exit 0).
    logs, rc = run_part(
        tmp_path,
        SHOWCASE_DIR / "stopwatch",
        "Stopwatch::stopwatchSystem",
    )
    assert rc == 0, f"expected exit 0 (verdict pass); stderr:\n{logs}"


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
        "Microwave::MicrowaveBehavior",
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
        "Microwave::MicrowaveBehavior",
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
    # microwaveSystem: testbench sends StartCmd at 0.1 s, expects Finished
    # before the 5 s timeout; verdict stays 0 (testPassed) -> exit 0.
    logs, rc = run_part(
        tmp_path, SHOWCASE_DIR / "microwave", "Microwave::microwaveSystem"
    )
    assert rc == 0, f"expected exit 0 (verdict pass); stderr:\n{logs}"


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


def test_batch_reactor_rig_verdict(tmp_path: Path) -> None:
    # BatchReactorRig: testbench sends StartRecipe at 0.2 s, waits for
    # BatchDone (announced when draining reaches level<=0 and batches+1
    # meets batchTarget). With sped-up _REACTOR_VALUES (batchTarget=1,
    # rates=60/65/60/60) and the model default sampleTime=1.0 s, the
    # full fill/heat/react/cool/drain cycle completes in ~7.2 s logical
    # (0.2 stimulus + 1.0 fill + 1.0 heat + 3.0 react + 1.0 cool +
    # 1.0 drain); the 30 s fail window is generous.
    #
    # No `announcing` latch needed: BatchDone is sent on a GUARDED
    # eventless completion transition exiting `draining`, not in the same
    # reaction that consumed StartRecipe, so lfc 0.11 sees no
    # read-input+write-output cycle.
    process, states = run_rig(
        tmp_path,
        SHOWCASE_DIR / "batch-reactor",
        "BatchReactor::BatchReactorRig",
        timeout="40 sec",
        values={"BatchReactor::BatchReactor": _REACTOR_VALUES},
    )
    assert process.returncode == 0, process.stderr
    # draining is the unique plant landmark proving a complete batch cycle:
    # the reactor only reaches draining after fill→heat→react→cool, and
    # BatchDone is sent the moment draining detects level<=0 and
    # batches+1 >= batchTarget.
    assert "draining" in states["plant"]
    assert states["plant"][-1] == "done"
    assert states["tb"][-1] == "done"


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


def test_charging_station_rig_verdict(tmp_path: Path) -> None:
    # ChargingStationRig: testbench sends PlugIn at 0.2 s, AuthResult(7,
    # denied) at 1.2 s abs, AuthResult(1, granted) at 1.6 s abs.  The plant
    # completes the three-phase charge cycle and announces SessionReport from
    # the ``finishing`` state's timed reaction (accept after 0.5 s).
    # SessionReport is sent by a timer, not an input reaction, so lfc 0.11
    # requires no announcing latch -- no read-input + write-output cycle.
    #
    # values override: tight thermal (tempLimit=45, coolThreshold=40) matches
    # the standalone session test so the expected plant sequence is the same;
    # the 40 s timeout is generous for the ~14 s logical run.
    process, states = run_rig(
        tmp_path,
        SHOWCASE_DIR / "charging-station",
        "ChargingStation::ChargingStationRig",
        timeout="40 sec",
        values={
            "ChargingStation::ChargingStation": {
                "tempLimit": 45.0,
                "coolThreshold": 40.0,
            }
        },
    )
    assert process.returncode == 0, process.stderr
    # ``finishing`` is the unique plant landmark proving the charge session
    # completed: the station only reaches finishing after successful auth,
    # ramp-up, bulk, optional derating, and top-off phases, and SessionReport
    # is emitted there before the station returns to idle.
    assert "finishing" in states["plant"]
    assert states["plant"][-1] == "idle"
    assert states["tb"][-1] == "done"


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


def test_level_crossing_rig_verdict(tmp_path: Path) -> None:
    # LevelCrossingRig: testbench sends TrainApproaching at 0.3 s then
    # TrainPassed at 4 s (abs); waits for Reopened — announced by the
    # plant when the opening sequence completes and the crossing returns
    # to open after a full safe passage cycle. Reopened is sent on a
    # COMPLETION transition from `opening` (not in the same reaction that
    # consumed TrainPassed), so no `announcing` latch is needed and
    # lfc 0.11 sees no causality cycle.
    #
    # Timeline (logical fast time):
    #   t=0.3 s   TrainApproaching sent → plant: open→securing
    #   t≈1.8 s   securing completes (0.4+0.4+0.7+rounding) → closed
    #   t=4.0 s   TrainPassed sent → closed.passage→done
    #   t≈4.5 s   bellCycle also done → join fires → opening
    #   t≈5.6 s   opening completes (0.7+0.4) → Reopened sent → open
    #   tb: reopenWait accepts Reopened → done → request_stop → exit 0
    process, states = run_rig(
        tmp_path,
        SHOWCASE_DIR / "level-crossing",
        "LevelCrossing::LevelCrossingRig",
        timeout="30 sec",
    )
    assert process.returncode == 0, process.stderr
    # The plant must visit the full securing→closed→opening path to prove
    # a safe cycle; pinning individual substates gives landmark confidence.
    assert "securing.warnLights" in states["plant"]
    assert "closed.passage.waiting" in states["plant"]
    assert "opening.barrierUp" in states["plant"]
    assert states["plant"][-1] == "open"
    assert states["tb"][-1] == "done"


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


def test_external_ramp_runs(tmp_path: Path) -> None:
    # `step` is supplied by ramp.py via the external build param; the
    # self-loop re-enters `run` every 0.1 s, re-announcing it. Proves the
    # external call compiles and runs (exit 0; every announcement is "run").
    states = run_machine(
        tmp_path,
        SM_EXAMPLES_DIR / "sm15-external",
        "SM15::Ramp",
        timeout="1 sec",
        python_file=SM_EXAMPLES_DIR / "sm15-external" / "ramp.py",
    )
    assert states, "no state announcements"
    assert all(s == "run" for s in states)


def run_part(
    tmp_path: Path,
    model_dir: Path,
    usage_qn: str,
    *,
    timeout: str = "10 sec",
) -> tuple[str, int]:
    """Build, compile, and run a part system; return (debug logs, returncode).

    The generated program has its own ``main reactor``, so it compiles
    directly (written as ``Main.lf`` -- no reactor is named ``Main`` -- to
    dodge the filename/reactor-name collision). The program stays silent by
    default; a ``sitecustomize.py`` on ``PYTHONPATH`` enables DEBUG logging so
    the entry/exit observation lines reach stderr, modelling how the run (not
    the program) turns observation on.
    """
    model = load_model(model_dir)
    program = build_part_program(
        model,
        usage_qn,
        target_options=(("fast", "true"), ("timeout", timeout)),
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "Main.lf").write_text(to_lf(program))
    (src / "sitecustomize.py").write_text(
        "import logging\nlogging.basicConfig(level=logging.DEBUG)\n"
    )
    compile_result = subprocess.run(
        ["lfc", str(src / "Main.lf")], capture_output=True, timeout=600
    )
    assert compile_result.returncode == 0, compile_result.stderr.decode(
        errors="replace"
    )
    env = {**os.environ, "PYTHONPATH": str(src)}
    process = subprocess.run(
        [str(tmp_path / "bin" / "Main")],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    return process.stderr, process.returncode


def test_part01_runs_and_logs(tmp_path: Path) -> None:
    logs, rc = run_part(
        tmp_path, SM_EXAMPLES_DIR / "part01-two-parts", "Part01::pingSystem"
    )
    assert rc == 0  # tester verdict ok (Pong arrived before the timeout)
    assert "entered Plant.pinged" in logs  # plant got the Ping (port-routed)
    assert "entered Tester.waitPong" in logs


def test_multi_exhibit_part_runs_and_passes_verdict(tmp_path: Path) -> None:
    # PartMulti::sys contains one part `rig : Rig` where Rig exhibits both
    # PlantBehavior and TesterBehavior. The tester sends Ping after 0.1 s,
    # the plant receives it (name-based cross-wiring inside Rig), replies
    # with Pong, and the tester reaches `done` (verdict == 0 → request_stop
    # → exit 0).  Verifies that compose_exhibits wires the internal signals
    # and the program compiles and runs cleanly.
    logs, rc = run_part(
        tmp_path,
        SM_EXAMPLES_DIR / "part-multi-exhibit",
        "PartMulti::sys",
        timeout="10 sec",
    )
    assert rc == 0, f"expected exit 0 (verdict pass); stderr:\n{logs}"


def test_part01_silent_without_debug(tmp_path: Path) -> None:
    # Without the run enabling DEBUG, the generated program emits no
    # observation lines (spec 5.5: silent unless the run turns DEBUG on).
    model = load_model(SM_EXAMPLES_DIR / "part01-two-parts")
    program = build_part_program(
        model,
        "Part01::pingSystem",
        target_options=(("fast", "true"), ("timeout", "10 sec")),
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "Main.lf").write_text(to_lf(program))
    compile_result = subprocess.run(
        ["lfc", str(src / "Main.lf")], capture_output=True, timeout=600
    )
    assert compile_result.returncode == 0, compile_result.stderr.decode(
        errors="replace"
    )
    process = subprocess.run(
        [str(tmp_path / "bin" / "Main")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0
    assert "entered" not in process.stderr
    assert "exited" not in process.stderr
