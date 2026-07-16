from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sysmlc.backends.rosetta.builder import build_program
from sysmlc.backends.rosetta.parts import build_part_program
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR
from tests.backends.test_sm_examples import SM_EXAMPLES_DIR

if TYPE_CHECKING:
    from sysmlc.backends.rosetta.program import LfProgram, Mode

PART_EXT = Path("models/sm-examples/part-external")


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
    (pt,) = program.reactor.state_vars
    assert pt.name == "pt"
    assert pt.init == "Point(x=0.5)"
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


def test_sm13_at_arms_scheduled_action_from_entry() -> None:
    program = _build("sm13-time-trigger", "SM13::MachineAt")
    idle = _mode(program, "idle")
    assert any(a.name == "at_idle_act" for a in idle.actions)
    entry = idle.reactions[0]
    assert "at_idle_act" in entry.effects
    assert any("lf.time.logical_elapsed()" in line for line in entry.body)
    assert any("at_idle_act.schedule(" in line for line in entry.body)
    # a dedicated reaction fires the transition off the scheduled action
    (fire,) = [r for r in idle.reactions if r.triggers == ("at_idle_act",)]
    assert "reset(running)" in fire.effects


def test_sm13_at_reentry_guards_negative_delta() -> None:
    # Re-entry after the instant passed must not fire: the schedule is gated
    # on a non-negative delta.
    program = _build("sm13-time-trigger", "SM13::MachineAtReentry")
    idle = _mode(program, "idle")
    entry = idle.reactions[0]
    assert any(">= 0" in line for line in entry.body)


def test_at_literal_instant_renders_nanosecond_constant() -> None:
    # `accept at deadlineTime + 2 [s]` is not a bare attribute reference, so
    # the instant is folded to a Python float at build time (`deadlineTime`'s
    # default of 4 [s] plus the 2 [s] offset); this exercises the
    # literal-float branch of the `at` renderer
    # (`instant_ns = str(round(instant * 1e9))`), as opposed to
    # `accept at deadlineTime` (SM13::MachineAt), which renders the
    # attribute-reference expression form instead.
    model = load_model(FIXTURES_DIR / "at-literal")
    program = build_program(model, "AtLiteral::Machine")
    idle = _mode(program, "idle")
    entry = idle.reactions[0]
    assert any(
        "_at_delta = 6000000000 - lf.time.logical_elapsed()" in line
        for line in entry.body
    )
    assert not any("int((" in line for line in entry.body)


# -- after + if: supported by rosetta although quake must reject it --


def test_after_with_guard_is_supported() -> None:
    model = load_model(FIXTURES_DIR / "after-guard")
    program = build_program(model, "AfterGuard::Machine")
    idle = _mode(program, "idle")
    (timer,) = idle.timers
    assert timer.offset == "5 sec"
    timer_reaction = idle.reactions[1]
    assert timer_reaction.body[0] == "if self.ready:"


# -- sm08: hierarchy becomes nested reactors --


def test_sm08_nested_builds_child_reactor() -> None:
    program = _build("sm08-nested-composite", "SM08::MachineNested")
    assert [r.name for r in program.reactors] == [
        "MachineNested_running",
        "MachineNested",
    ]
    child = program.reactors[0]
    assert child.outputs == ("completed", "current_state")
    assert [m.name for m in child.modes] == ["warming", "hot"]
    assert child.modes[0].initial


def test_sm08_composite_mode_instantiates_and_reemits() -> None:
    program = _build("sm08-nested-composite", "SM08::MachineNested")
    running = _mode(program, "running")
    (inst,) = running.instantiations
    assert (inst.name, inst.reactor) == ("c_running", "MachineNested_running")
    (reemit,) = [
        r
        for r in running.reactions
        if r.triggers == ("c_running.current_state",)
    ]
    assert reemit.body == (
        'current_state.set("running." + c_running.current_state.value)',
    )


def test_sm08_deep_nesting_builds_grandchild_reactor() -> None:
    program = _build("sm08-nested-composite", "SM08::MachineDeep")
    assert [r.name for r in program.reactors] == [
        "MachineDeep_running_warming",
        "MachineDeep_running",
        "MachineDeep",
    ]
    middle = program.reactors[1]
    (warming,) = [m for m in middle.modes if m.name == "warming"]
    (reemit,) = [
        r
        for r in warming.reactions
        if r.triggers == ("c_warming.current_state",)
    ]
    assert reemit.body == (
        'current_state.set("warming." + c_warming.current_state.value)',
    )


def test_sm08_sibling_names_reused_across_scopes_build() -> None:
    program = _build("sm08-nested-composite", "SM08::MachineNameCollision")
    assert [r.name for r in program.reactors] == [
        "MachineNameCollision_groupA",
        "MachineNameCollision_groupB",
        "MachineNameCollision",
    ]


def test_sm08_completion_out_of_composite_fires_on_completed() -> None:
    # `transition first groupA then groupB` is a completion transition: it
    # must trigger on the child's completed port, never fold into entry.
    program = _build("sm08-nested-composite", "SM08::MachineNameCollision")
    group_a = _mode(program, "groupA")
    entry = group_a.reactions[0]
    assert all("groupB.set()" not in line for line in entry.body)
    (completion,) = [
        r for r in group_a.reactions if r.triggers == ("c_groupA.completed",)
    ]
    assert completion.effects == ("reset(groupB)",)
    assert completion.body == ("groupB.set()",)


def test_sm08_deep_exit_raises_dedicated_child_port() -> None:
    program = _build("sm08-nested-composite", "SM08::MachineCrossOut")
    child = program.reactors[0]
    assert child.outputs == ("completed", "current_state", "exit_0")
    (hot,) = [m for m in child.modes if m.name == "hot"]
    entry = hot.reactions[0]
    assert "exit_0.set(True)" in entry.body
    assert "exit_0" in entry.effects
    running = _mode(program, "running")
    (exit_reaction,) = [
        r for r in running.reactions if r.triggers == ("c_running.exit_0",)
    ]
    assert exit_reaction.effects == ("reset(stopped)",)
    assert exit_reaction.body == ("stopped.set()",)


# -- sm09: parallel becomes sibling region reactors --


def test_sm09_parallel_root_instantiates_regions_at_reactor_scope() -> None:
    program = _build("sm09-parallel", "SM09::MachineParallel")
    assert [r.name for r in program.reactors] == [
        "MachineParallel_lights",
        "MachineParallel_sound",
        "MachineParallel",
    ]
    machine = program.reactor
    assert machine.modes == ()
    assert [i.name for i in machine.instantiations] == ["c_lights", "c_sound"]
    (reemit,) = [
        r
        for r in machine.reactions
        if r.triggers == ("c_lights.current_state",)
    ]
    assert reemit.body == (
        'current_state.set("lights." + c_lights.current_state.value)',
    )


def test_sm09_parallel_root_join_requests_stop() -> None:
    program = _build("sm09-parallel", "SM09::MachineParallel")
    machine = program.reactor
    (join,) = [
        r
        for r in machine.reactions
        if r.triggers == ("c_lights.completed", "c_sound.completed")
    ]
    assert "request_stop()" in join.body[-1]
    assert {v.name for v in machine.state_vars} == {
        "lights_done",
        "sound_done",
    }


def test_sm09_nested_parallel_mode_holds_region_instances() -> None:
    program = _build("sm09-parallel", "SM09::MachineNestedParallel")
    assert [r.name for r in program.reactors] == [
        "MachineNestedParallel_dual_lights",
        "MachineNestedParallel_dual_sound",
        "MachineNestedParallel",
    ]
    dual = _mode(program, "dual")
    assert [i.name for i in dual.instantiations] == ["c_lights", "c_sound"]
    entry = dual.reactions[0]
    assert "self.count = 1" in entry.body  # `entry assign count := 1`
    (reemit,) = [
        r for r in dual.reactions if r.triggers == ("c_sound.current_state",)
    ]
    assert reemit.body == (
        'current_state.set("dual.sound." + c_sound.current_state.value)',
    )


# -- companion module wiring (Task 5) --


def test_build_program_sets_types_module_when_composite_type_exists() -> None:
    # sm05 MachineChainGuard has `attribute pt : Point` — a composite type.
    program = _build("sm05-chained-references", "SM05::MachineChainGuard")
    assert program.types_module_name == "MachineChainGuard_types"
    assert any("class Point:" in ln for ln in program.types_module_lines)
    files = dict(program.target_options).get("files")
    assert files == '["MachineChainGuard_types.py"]'


def test_build_program_no_types_module_when_no_types() -> None:
    # sm01 has no composite types: no companion module, no files:.
    program = _build("sm01-helloworld", "SM01::Machine")
    assert program.types_module_name is None
    assert program.types_module_lines == ()
    assert "files" not in dict(program.target_options)


def test_part_program_sets_types_module_and_files() -> None:
    prog = build_part_program(
        load_model(PART_EXT),
        "PartExt::counterSystem",
        external=("bump", frozenset({"bump"})),
    )
    # part-external has no composite attribute types, only external functions.
    # files: must still include the --python module.
    files = dict(prog.target_options).get("files")
    assert files == '["bump.py"]'


def test_dispatch_fired_sets_flag_per_branch() -> None:
    from sysmlc.backends.rosetta.builder import RosettaBuilder
    from sysmlc.semantics.statemachine.driver import StateMachineDriver
    from sysmlc.semantics.statemachine.facts import SignalTrigger

    model = load_model(FIXTURES_DIR / "rtc")
    b = RosettaBuilder("TwoSignals")
    StateMachineDriver(model).run("Rtc::TwoSignals", b)
    gen = b._scope_codegen("")
    group = [
        t
        for t in b._scope_transitions[""]
        if t.source == "idle"
        and isinstance(t.trigger, SignalTrigger)
        and t.trigger.signal_name == "A"
    ]
    body, _ = b._dispatch(group, [], "", gen, fired="idle_fired")
    assert any("self.idle_fired = True" in line for line in body)
    # Without `fired`, no flag write appears.
    body2, _ = b._dispatch(group, [], "", gen)
    assert not any("idle_fired" in line for line in body2)


# -- sm16: accept when change triggers --


def test_sm16_bare_when_arms_and_checks_on_change() -> None:
    program = _build("sm16-change-trigger", "SM16::MachineWhenBare")
    reactor = program.reactor
    assert any(a.name == "_change_act" for a in reactor.actions)
    idle = _mode(program, "idle")
    assert any(v.name == "idle_w_armed" for v in reactor.state_vars)
    entry = idle.reactions[0]
    assert "_change_act.schedule(0)" in entry.body
    assert "_change_act" in entry.effects
    (chk,) = [r for r in idle.reactions if r.triggers == ("_change_act",)]
    assert "if not self.idle_w_armed:" in chk.body
    assert any("if (self.hot):" in line for line in chk.body)
    assert any("self.idle_w_armed = True" in line for line in chk.body)
    assert "reset(running)" in chk.effects


def test_sm16_guarded_when_consumes_before_guard() -> None:
    # consume-on-false-guard: armed is set when the CONDITION holds, before
    # the guard is evaluated, so a false guard still consumes the occurrence.
    program = _build("sm16-change-trigger", "SM16::MachineWhenGuard")
    idle = _mode(program, "idle")
    (chk,) = [r for r in idle.reactions if r.triggers == ("_change_act",)]
    body = "\n".join(chk.body)
    armed_idx = body.index("self.idle_w_armed = True")
    guard_idx = body.index("if self.enabled:")
    assert armed_idx < guard_idx


def test_sm16_composed_condition_watched_whole() -> None:
    # A model whose transition effect assigns a watched attribute schedules
    # the change action from that reaction. MachineWhenComposed's `running`
    # completion does not assign; use MachineWhenTwo which has two whens but
    # no assignment, so only entry arming schedules. Assert the machine with
    # a self-driving assignment (WhenCounter fixture, Task 4) instead.
    program = _build("sm16-change-trigger", "SM16::MachineWhenComposed")
    idle = _mode(program, "idle")
    (chk,) = [r for r in idle.reactions if r.triggers == ("_change_act",)]
    # the composed condition is watched as a whole
    assert any(
        "if (self.hot and self.enabled):" in line for line in chk.body
    )


def test_change_notify_is_gated_by_the_assigning_branch() -> None:
    # A `when`-transition whose effect assigns a watched attribute must schedule
    # _change_act right after the assignment (inside the guard), NOT once at the
    # reaction's top level -- a top-level notify would re-fire this
    # _change_act-triggered check reaction forever.
    program = build_program(
        load_model(FIXTURES_DIR / "when-assign"), "WhenAssign::Machine"
    )
    idle = _mode(program, "idle")
    (chk,) = [r for r in idle.reactions if r.triggers == ("_change_act",)]
    assert "_change_act" in chk.effects
    assign_i = next(
        i for i, line in enumerate(chk.body) if "self.count = " in line
    )
    assign_line = chk.body[assign_i]
    indent = assign_line[: len(assign_line) - len(assign_line.lstrip())]
    # the notify immediately follows the assignment, at the same (guarded) indent
    assert chk.body[assign_i + 1] == f"{indent}_change_act.schedule(0)"
    # and every notify in the check reaction is indented (none unconditional)
    assert all(
        line.startswith(" ")
        for line in chk.body
        if line.strip() == "_change_act.schedule(0)"
    )


def test_no_change_action_without_when() -> None:
    program = _build("sm01-helloworld", "SM01::Machine")
    assert not any(a.name == "_change_act" for a in program.reactor.actions)
