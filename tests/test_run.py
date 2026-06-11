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

from sysmlc.backends.rosetta.builder import build_program
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR
from tests.backends.showcase import SHOWCASE_DIR
from tests.backends.sm_examples import SM_EXAMPLES_DIR

if TYPE_CHECKING:
    from pathlib import Path

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


def run_machine(
    tmp_path: Path,
    model_dir: Path,
    qn: str,
    *,
    timeout: str = "2 sec",
    drivers: str = "",
) -> list[str]:
    """Generate, compile, and run one machine; return the state sequence."""
    name = qn.split("::")[-1]
    model = load_model(model_dir)
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
    result = subprocess.run(
        [str(tmp_path / "bin" / "Harness")],
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
    assert states == ["showRed", "showGreen", "showYellow"]


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


def test_showcase_furuta_pendulum(tmp_path: Path) -> None:
    # Decreasing |theta| swings up, catches, then stabilizes; a late spike
    # past dropAngle knocks it back to swing-up. SimpleNamespace stands in
    # for AngleReading (payloads are duck-typed across files).
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
    assert states == ["swingUp", "catching", "stabilizing", "swingUp"]
