from __future__ import annotations

from typing import TYPE_CHECKING

from sysmlc.backends.rosetta.builder import build_program
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR
from tests.backends.sm_examples import SM_EXAMPLES_DIR

if TYPE_CHECKING:
    from sysmlc.backends.rosetta.program import LfProgram, Mode


def _build(model_dir: str, qn: str) -> LfProgram:
    model = load_model(SM_EXAMPLES_DIR / model_dir)
    return build_program(model, qn)


def _mode(program: LfProgram, name: str) -> Mode:
    (mode,) = [m for m in program.reactor.modes if m.name == name]
    return mode


def test_sm01_modes_mirror_states() -> None:
    program = _build("sm01-helloworld", "SM01::Machine")
    reactor = program.reactor
    assert reactor.name == "Machine"
    assert [mode.name for mode in reactor.modes] == ["idle", "running"]
    assert reactor.modes[0].initial
    assert not reactor.modes[1].initial
    assert reactor.outputs == ("current_state",)


def test_sm01_eventless_transition_dispatches_from_entry() -> None:
    program = _build("sm01-helloworld", "SM01::Machine")
    idle = program.reactor.modes[0]
    entry = idle.reactions[0]
    assert entry.triggers == ("reset", "startup")
    assert 'current_state.set("idle")' in entry.body
    assert "running.set()" in entry.body
    assert "reset(running)" in entry.effects


def test_sm01_serializes_to_valid_looking_lf() -> None:
    text = to_lf(_build("sm01-helloworld", "SM01::Machine"))
    assert "target Python" in text
    assert "initial mode idle {" in text
    assert "main reactor {" in text


# -- sm02: signal triggers become input ports --


def test_sm02_signal_becomes_input_port_and_reaction() -> None:
    program = _build("sm02-event-trigger", "SM02::MachinePortless")
    assert program.reactor.inputs == ("Tick",)
    idle = _mode(program, "idle")
    # [0] is the entry reaction; [1] is the Tick-triggered reaction
    tick = idle.reactions[1]
    assert tick.triggers == ("Tick",)
    assert tick.effects == ("reset(running)",)
    assert tick.body == ("running.set()",)


def test_sm02_named_payload_without_guard_use_still_builds() -> None:
    program = _build("sm02-event-trigger", "SM02::MachineNamed")
    assert program.reactor.inputs == ("Tick",)
    # The binding line is emitted even when nothing reads the payload; a
    # harmless unused local keeps the generation rule uniform.
    idle = _mode(program, "idle")
    assert idle.reactions[1].body[0] == "reading = Tick.value"


# -- sm03: guards --


def test_sm03_guard_wraps_the_branch() -> None:
    program = _build("sm03-guard", "SM03::MachineRef")
    idle = _mode(program, "idle")
    entry = idle.reactions[0]
    assert "if self.enabled:" in entry.body
    assert "    running.set()" in entry.body
    assert program.reactor.state_vars[0].name == "enabled"
    assert program.reactor.state_vars[0].init == "True"


# -- sm04: entry/exit assignments --


def test_sm04_entry_assignment_in_entry_reaction() -> None:
    program = _build("sm04-assignment", "SM04::MachineEntryIncrement")
    entry = _mode(program, "idle").reactions[0]
    assert 'current_state.set("idle")' in entry.body
    assert "self.counter = self.counter + 1" in entry.body


def test_sm04_exit_runs_before_target_set() -> None:
    program = _build("sm04-assignment", "SM04::MachineExitDecrement")
    entry = _mode(program, "idle").reactions[0]
    body = list(entry.body)
    assert body.index("self.counter = self.counter - 1") < body.index(
        "running.set()"
    )


# -- sm05: chained references --


def test_sm05_chained_guard_and_composite_state_var() -> None:
    program = _build("sm05-chained-references", "SM05::MachineChainGuard")
    assert program.preamble == ("from types import SimpleNamespace",)
    (pt,) = program.reactor.state_vars
    assert pt.name == "pt"
    assert pt.init == "SimpleNamespace(x=0.5)"
    entry = _mode(program, "idle").reactions[0]
    assert "if self.pt.x > 0.0:" in entry.body


# -- sm06: transition effects --


def test_sm06_effect_runs_before_target_set() -> None:
    program = _build("sm06-transition-effect", "SM06::MachineEffect")
    entry = _mode(program, "idle").reactions[0]
    body = list(entry.body)
    assert body.index("self.counter = self.counter + 1") < body.index(
        "armed.set()"
    )


# -- sm07: firing order exit -> effect (entry fires in the next mode) --


def test_sm07_exit_then_effect_order() -> None:
    program = _build("sm07-firing-order", "SM07::MachineFiringOrder")
    entry_a = _mode(program, "a").reactions[0]
    body = list(entry_a.body)
    exit_at = body.index("self.exitAt = self.seq")
    effect_at = body.index("self.effectAt = self.seq")
    assert exit_at < effect_at < body.index("b.set()")


# -- sm10: done --


def test_sm10_done_mode_requests_stop_once() -> None:
    program = _build("sm10-done", "SM10::MachineRootDone")
    assert [m.name for m in program.reactor.modes] == [
        "idle",
        "running",
        "done",
    ]
    done = _mode(program, "done")
    assert "request_stop()" in done.reactions[0].body


def test_sm10_two_dones_share_one_mode() -> None:
    program = _build("sm10-done", "SM10::MachineTwoDone")
    assert [m.name for m in program.reactor.modes].count("done") == 1
    entry = _mode(program, "idle").reactions[0]
    assert "if self.shortcut:" in entry.body
    assert "elif not self.shortcut:" in entry.body


def test_sm10_two_dones_entry_effects_dedup() -> None:
    program = _build("sm10-done", "SM10::MachineTwoDone")
    entry = _mode(program, "idle").reactions[0]
    assert entry.effects.count("current_state") == 1
    assert entry.effects[0] == "current_state"


# -- sm11: send effects --


def test_sm11_self_send_declares_action_and_double_trigger() -> None:
    program = _build("sm11-send-effect", "SM11::MachineSelfSend")
    assert program.reactor.inputs == ("Ping",)
    assert [a.name for a in program.reactor.actions] == ["Ping_act"]
    idle_entry = _mode(program, "idle").reactions[0]
    assert "Ping_act.schedule(0)" in idle_entry.body
    armed = _mode(program, "armed")
    # [0] is the entry reaction; [1] is the triggered reaction
    assert armed.reactions[1].triggers == ("Ping", "Ping_act")


# -- sm12: do actions fuse into entry --


def test_sm12_do_fuses_after_entry() -> None:
    program = _build("sm12-do-action", "SM12::MachineEntryThenDo")
    entry = _mode(program, "working").reactions[0]
    body = list(entry.body)
    assert body.index("self.log = 1") < body.index("self.log = self.log + 10")


def test_sm12_root_do_becomes_startup_reaction() -> None:
    program = _build("sm12-do-action", "SM12::MachineRootDo")
    (startup,) = program.reactor.reactions
    assert startup.triggers == ("startup",)
    assert startup.body == ("self.progress = self.progress + 1",)


def test_sm12_do_send_declares_scheduled_action_as_entry_effect() -> None:
    # lfc's Python target only passes effect-listed names into the reaction
    # function, so a send scheduled from an entry/do body must surface in
    # the entry reaction's effects clause.
    program = _build("sm12-do-action", "SM12::MachineDoSend")
    working = _mode(program, "working")
    entry = working.reactions[0]
    assert "Ping_act.schedule(0)" in entry.body
    assert "Ping_act" in entry.effects


# -- sm13: time triggers (the `after` timer lives on the source mode) --


def test_sm13_literal_after_becomes_mode_timer() -> None:
    program = _build("sm13-time-trigger", "SM13::MachineAfterSeconds")
    idle = _mode(program, "idle")
    (timer,) = idle.timers
    assert timer.offset == "5 sec"
    # [0] is the entry reaction; [1] is the timer-triggered reaction
    timer_reaction = idle.reactions[1]
    assert timer_reaction.triggers == (timer.name,)
    assert timer_reaction.body == ("running.set()",)


def test_sm13_minutes_normalize_to_seconds() -> None:
    program = _build("sm13-time-trigger", "SM13::MachineAfterMinutes")
    (timer,) = _mode(program, "idle").timers
    assert timer.offset == "120 sec"


def test_sm13_attribute_duration_schedules_on_entry() -> None:
    program = _build("sm13-time-trigger", "SM13::MachineAfterAttribute")
    idle = _mode(program, "idle")
    assert idle.timers == ()
    (action,) = idle.actions
    entry = idle.reactions[0]
    assert (
        f"{action.name}.schedule(int((self.pickDuration) * 1e9))" in entry.body
    )
    assert action.name in entry.effects


def test_sm13_chained_duration_renders_chain() -> None:
    program = _build("sm13-time-trigger", "SM13::MachineAfterChain")
    idle = _mode(program, "idle")
    entry = idle.reactions[0]
    assert any("self.holder.delay" in line for line in entry.body)


# -- after + if: supported by rosetta although quake must reject it --


def test_after_with_guard_is_supported() -> None:
    model = load_model(FIXTURES_DIR / "after-guard")
    program = build_program(model, "AfterGuard::Machine")
    idle = _mode(program, "idle")
    (timer,) = idle.timers
    assert timer.offset == "5 sec"
    timer_reaction = idle.reactions[1]
    assert timer_reaction.body[0] == "if self.ready:"
