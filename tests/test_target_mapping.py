from __future__ import annotations

from typing import TYPE_CHECKING

from sysmlc.sysml.foreign_artifact.base import ForeignArtifact
from sysmlc.sysml.loading import load_model
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.builder import build_program
from tests.conftest import FIXTURES_DIR

if TYPE_CHECKING:
    from sysmlc_rosetta.program import LfProgram, Mode


def _build(name: str) -> LfProgram:
    return build_program(
        load_model(FIXTURES_DIR / "target-mapping"), f"TargetMapping::{name}"
    )


def _mode(program: LfProgram, name: str) -> Mode:
    return next(mode for mode in program.reactor.modes if mode.name == name)


def test_enum_companion_import_initializer_and_effect() -> None:
    program = build_program(
        load_model(SM_EXAMPLES_DIR / "sm18-enum-literals"),
        "SM18::MachineStringEnum",
    )
    assert "class LightColor(Enum):" in program.types_module_lines
    for name in ("red", "green", "yellow"):
        assert f'    {name} = "{name}"' in program.types_module_lines
    assert any(
        "from MachineStringEnum_types import" in line and "LightColor" in line
        for line in program.preamble
    )
    color = next(var for var in program.reactor.state_vars if var.name == "c")
    assert color.init == "LightColor.red"
    assert (
        "self.c = LightColor.green" in _mode(program, "idle").reactions[0].body
    )


def test_timer_and_signal_self_loops_reset_the_mode() -> None:
    program = _build("TimerLoop")
    idle = _mode(program, "idle")
    (timer,) = idle.timers
    assert timer.offset == "1 sec"
    tick = idle.reactions[1]
    assert tick.triggers == (timer.name,)
    assert tick.body[0] == "if not self.idle_fired:"
    assert "    self.count = self.count + 1" in tick.body
    assert "    idle.set()" in tick.body
    assert tick.effects == ("reset(idle)",)
    (reset,) = [
        reaction
        for reaction in idle.reactions
        if reaction.triggers == ("Reset",)
    ]
    assert reset.effects == ("reset(idle)",)
    assert "    self.count = 0" in reset.body


def test_parameters_and_state_vars_use_their_defaults() -> None:
    program = _build("Counter")
    assert {
        parameter.name: parameter.default
        for parameter in program.reactor.parameters
    } == {"limit": "2.0", "step": "1.0"}
    (value,) = program.reactor.state_vars
    assert (value.name, value.init) == ("value", "0.0")


def test_periodic_assignment_and_entry_guard_share_context() -> None:
    run = _mode(_build("Counter"), "run")
    assert "if self.value >= self.limit:" in run.reactions[0].body
    assert "    complete.set()" in run.reactions[0].body
    assert (
        "self.value = min(self.value + self.step, self.limit)"
        in run.reactions[1].body
    )


def test_payload_branches_share_one_reaction_and_bind_the_occurrence() -> None:
    program = _build("PayloadDispatch")
    idle = _mode(program, "idle")
    assert len(idle.reactions) == 2
    reaction = idle.reactions[1]
    assert reaction.triggers == ("Reading",)
    assert reaction.body[0] == "reading = Reading.value"
    assert "if reading.value < self.limit:" in reaction.body
    assert "elif reading.value == self.limit:" in reaction.body
    assert "else:" in reaction.body
    assert "    overflow.set()" in reaction.body
    assert "class Reply:" in program.types_module_lines
    assert not any("Reply_act.schedule" in line for line in reaction.body)


def test_small_external_calc_imports_its_support_module() -> None:
    directory = SM_EXAMPLES_DIR / "sm15-external"
    program = build_program(
        load_model(directory),
        "SM15::Ramp",
        external=[ForeignArtifact(directory / "ramp.py", "python")],
    )
    assert "from ramp import step" in program.preamble
    assert (
        "self.x = step(self.x, 0.1)" in _mode(program, "run").reactions[1].body
    )


def test_nested_mode_forwards_only_child_inputs() -> None:
    program = _build("NestedSignals")
    assert [reactor.name for reactor in program.reactors] == [
        "NestedSignals_work",
        "NestedSignals",
    ]
    work = _mode(program, "work")
    assert [
        (connection.source, connection.target)
        for connection in work.connections
    ] == [("Advance", "c_work.Advance")]


def test_constraints_run_at_startup_and_after_assignments() -> None:
    program = _build("Counter")
    (startup,) = [
        reaction
        for reaction in program.reactor.reactions
        if reaction.triggers == ("startup",)
    ]
    assert startup.body == (
        'assert self.value >= 0.0, "SysML constraint nonnegative violated"',
        'assert self.limit > 0.0, "SysML constraint positiveLimit violated"',
    )
    run = _mode(program, "run")
    assert run.reactions[1].body[-2:] == startup.body
    assert not any(line.startswith("assert ") for line in run.reactions[0].body)


def test_negated_constraint_renders_wrapped_assert() -> None:
    program = build_program(
        load_model(FIXTURES_DIR / "negated-constraint"),
        "NegatedConstraint::Machine",
    )
    (startup,) = [
        reaction
        for reaction in program.reactor.reactions
        if reaction.triggers == ("startup",)
    ]
    assert startup.body == (
        'assert not (self.level > 2.0), "SysML constraint tooHigh violated"',
    )


def test_deep_exit_propagates_through_region_and_composite() -> None:
    program = _build("DeepExit")
    monitor, work, _root = program.reactors
    assert "exit_0" in monitor.outputs
    assert "exit_0" in work.outputs
    (workers,) = [mode for mode in work.modes if mode.name == "workers"]
    (reraise,) = [
        reaction
        for reaction in workers.reactions
        if reaction.triggers == ("c_monitor.exit_0",)
    ]
    assert reraise.effects == ("exit_0",)
    (resolve,) = [
        reaction
        for reaction in _mode(program, "work").reactions
        if reaction.triggers == ("c_work.exit_0",)
    ]
    assert resolve.effects == ("reset(stopped)",)


def test_mixed_join_has_reset_flags_and_waits_for_both_regions() -> None:
    program = _build("MixedJoin")
    assert [reactor.name for reactor in program.reactors] == [
        "MixedJoin_work_timed",
        "MixedJoin_work_signaled",
        "MixedJoin",
    ]
    assert {var.name for var in program.reactor.state_vars} == {
        "work_timed_done",
        "work_signaled_done",
    }
    assert all(var.reset for var in program.reactor.state_vars)
    work = _mode(program, "work")
    assert "self.work_timed_done = False" in work.reactions[0].body
    assert "self.work_signaled_done = False" in work.reactions[0].body
    (join,) = [
        reaction
        for reaction in work.reactions
        if reaction.triggers == ("c_timed.completed", "c_signaled.completed")
    ]
    assert "if self.work_timed_done and self.work_signaled_done:" in join.body
    assert join.effects == ("reset(complete)",)


def test_guard_dispatch_preserves_declaration_priority() -> None:
    body = _mode(_build("GuardOrder"), "idle").reactions[0].body
    assert body.index("if self.ready:") < body.index("elif True:")


def test_completion_dispatch_keeps_both_guarded_branches() -> None:
    program = build_program(
        load_model(SM_EXAMPLES_DIR / "sm10-done"), "SM10::MachineGuardedJoin"
    )
    (completion,) = [
        reaction
        for reaction in _mode(program, "working").reactions
        if reaction.triggers == ("c_working.completed",)
    ]
    assert "if self.go:" in completion.body
    assert "elif not self.go:" in completion.body
    assert completion.effects == ("reset(approved)", "reset(rejected)")
