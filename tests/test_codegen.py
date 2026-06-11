from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sysmlc.backends.rosetta.codegen import LfPythonCodeGen, PreambleNeeds
from sysmlc.errors import UnsupportedConstructError
from sysmlc.semantics.statemachine import actions
from tests.backends.rosetta.conftest import FIXTURES_DIR, record
from tests.backends.showcase import SHOWCASE_DIR
from tests.backends.sm_examples import SM_EXAMPLES_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from syside import ActionUsage, Expression


def _only_guard(model_dir: str, qn: str) -> Expression:
    recorded = record(SM_EXAMPLES_DIR / model_dir, qn)
    guards = [t.guard for t in recorded.transitions if t.guard is not None]
    assert len(guards) == 1
    return guards[0]


def test_feature_reference_renders_self_prefixed() -> None:
    guard = _only_guard("sm03-guard", "SM03::MachineRef")
    gen = LfPythonCodeGen(frozenset({"enabled"}))
    assert gen.render_expression(guard) == "self.enabled"


def test_chained_reference_prefixes_the_base() -> None:
    guard = _only_guard("sm05-chained-references", "SM05::MachineChainGuard")
    gen = LfPythonCodeGen(frozenset({"pt"}))
    assert gen.render_expression(guard) == "self.pt.x > 0.0"


def test_unknown_reference_is_rejected() -> None:
    guard = _only_guard("sm03-guard", "SM03::MachineRef")
    gen = LfPythonCodeGen(frozenset())
    with pytest.raises(UnsupportedConstructError, match="enabled"):
        gen.render_expression(guard)


def _entry_action(model_dir: str, qn: str, state: str) -> ActionUsage:
    recorded = record(SM_EXAMPLES_DIR / model_dir, qn)
    (fact,) = [s for s in recorded.states if s.name == state]
    assert fact.entry_action is not None
    return fact.entry_action


def test_assignment_target_is_self_prefixed() -> None:
    entry = _entry_action(
        "sm04-assignment", "SM04::MachineEntryIncrement", "idle"
    )
    (assign,) = actions.inline_actions(entry)
    gen = LfPythonCodeGen(frozenset({"counter"}))
    assert gen.render_action(assign) == "self.counter = self.counter + 1"


def test_assignment_to_unknown_base_is_rejected() -> None:
    entry = _entry_action(
        "sm04-assignment", "SM04::MachineEntryIncrement", "idle"
    )
    (assign,) = actions.inline_actions(entry)
    gen = LfPythonCodeGen(frozenset())
    with pytest.raises(UnsupportedConstructError, match="counter"):
        gen.render_action(assign)


def test_send_renders_schedule_without_payload() -> None:
    recorded = record(
        SM_EXAMPLES_DIR / "sm11-send-effect", "SM11::MachineSelfSend"
    )
    effects = [t.effect for t in recorded.transitions if t.effect is not None]
    (send,) = actions.inline_actions(effects[0])
    gen = LfPythonCodeGen(frozenset())
    assert gen.render_action(send) == "Ping_act.schedule(0)"


def test_send_renders_schedule_with_payload_constructor() -> None:
    recorded = record(
        SM_EXAMPLES_DIR / "sm11-send-effect", "SM11::MachinePayload"
    )
    effects = [t.effect for t in recorded.transitions if t.effect is not None]
    (send,) = actions.inline_actions(effects[0])
    gen = LfPythonCodeGen(frozenset({"current"}))
    assert gen.render_action(send) == (
        "Reading_act.schedule(0, Reading(value=self.current))"
    )


def _entry_assigns(model_dir: Path, qn: str, state: str) -> list[ActionUsage]:
    """Return the inline actions from a named state's entry action."""
    recorded = record(model_dir, qn)
    (fact,) = [s for s in recorded.states if s.name == state]
    return actions.inline_actions(fact.entry_action)


def test_enum_literal_renders_and_registers() -> None:
    (assign, _) = _entry_assigns(
        SHOWCASE_DIR / "traffic-light", "TrafficLight::TrafficLight", "showRed"
    )
    needs = PreambleNeeds()
    gen = LfPythonCodeGen(frozenset({"color", "requested"}), needs=needs)
    assert gen.render_action(assign) == "self.color = LightColor.red"
    assert list(needs.enum_defs) == ["LightColor"]


def test_payload_local_renders_bare_and_attr_check_skipped() -> None:
    recorded = record(
        SHOWCASE_DIR / "vending-machine", "VendingMachine::VendingMachine"
    )
    guards = [t.guard for t in recorded.transitions if t.guard is not None]
    gen = LfPythonCodeGen(
        frozenset({"credit", "price"}),
        local_names=frozenset({"coin"}),
    )
    rendered = {gen.render_expression(g) for g in guards}
    assert "self.credit + coin.value < self.price" in rendered


def test_whitelisted_functions_render() -> None:
    recorded = record(FIXTURES_DIR / "functions", "Functions::Machine")
    (guard,) = [t.guard for t in recorded.transitions if t.guard is not None]
    needs = PreambleNeeds()
    gen = LfPythonCodeGen(frozenset({"x"}), needs=needs)
    assert gen.render_expression(guard) == (
        "abs(self.x) > 1.0 and math.cos(self.x) < 1.0"
    )
    assert needs.uses_math is True


def test_unlisted_function_is_rejected() -> None:
    recorded = record(
        FIXTURES_DIR / "functions", "Functions::MachineUnsupported"
    )
    (guard,) = [t.guard for t in recorded.transitions if t.guard is not None]
    gen = LfPythonCodeGen(frozenset({"x"}))
    with pytest.raises(UnsupportedConstructError, match="sqrt"):
        gen.render_expression(guard)
