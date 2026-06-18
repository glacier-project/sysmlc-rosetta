from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sismic.interpreter import Interpreter
from sismic.io import import_from_yaml

from sysmlc.backends.quake.backend import QuakeBackend
from sysmlc.backends.quake.serialize import to_yaml as quake_to_yaml
from sysmlc.backends.rosetta.builder import build_program
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model
from tests.backends.rosetta.conftest import FIXTURES_DIR
from tests.backends.rosetta.lf_harness import run_machine

MODEL_DIR = FIXTURES_DIR / "rtc"  # created in Task 5


def test_multi_trigger_emits_fired_flag() -> None:
    lf = to_lf(build_program(load_model(MODEL_DIR), "Rtc::TwoSignals"))
    # The per-mode flag is declared (plain state, not `reset state`).
    assert "state idle_fired = {= False =}" in lf
    assert "reset state idle_fired" not in lf
    # Reset in the entry reaction body.
    assert "self.idle_fired = False" in lf
    # Each transition body is guarded by the flag.
    assert "if not self.idle_fired:" in lf
    assert "self.idle_fired = True" in lf


def test_multi_trigger_reactions_in_declaration_order() -> None:
    lf = to_lf(build_program(load_model(MODEL_DIR), "Rtc::TwoSignals"))
    # A is declared before B, so reaction(A) precedes reaction(B).
    assert lf.index("reaction(A)") < lf.index("reaction(B)")


def test_single_trigger_has_no_fired_flag() -> None:
    # A single-trigger state must not gain the _fired flag (it stays
    # byte-identical); SingleTrigger's `idle` accepts only A.
    lf = to_lf(build_program(load_model(MODEL_DIR), "Rtc::SingleTrigger"))
    assert "_fired" not in lf


def test_fired_name_collision_is_rejected() -> None:
    with pytest.raises(UnsupportedConstructError, match="collides"):
        build_program(load_model(MODEL_DIR), "Rtc::NameClashFired")


# ---------------------------------------------------------------------------
# Differential tests (lf-marked): rosetta LF binary vs. quake/sismic
# ---------------------------------------------------------------------------

BOTH_AB = (
    "  timer fire(100 msec)\n"
    "  reaction(fire) -> m.A, m.B {=\n"
    "    m.A.set(True)\n"
    "    m.B.set(True)\n"
    "  =}"
)
A_AT_1S = (
    "  timer fire(1 sec)\n  reaction(fire) -> m.A {=\n    m.A.set(True)\n  =}"
)


def _sismic_config(qn: str, events: list[str]) -> set[str]:
    """Build qn through quake, run through sismic, queue events in order."""
    model = load_model(MODEL_DIR)
    chart = QuakeBackend().build(model, qn)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "chart.yaml"
        p.write_text(quake_to_yaml(chart))
        sc = import_from_yaml(filepath=str(p))
    intp = Interpreter(sc)
    intp.execute()
    for e in events:
        intp.queue(e)
    intp.execute()
    return set(intp.configuration)


@pytest.mark.lf
def test_two_signals_first_declared_wins(tmp_path: Path) -> None:
    rosetta = run_machine(
        tmp_path, MODEL_DIR, "Rtc::TwoSignals", drivers=BOTH_AB
    )
    quake = _sismic_config("Rtc::TwoSignals", ["A", "B"])  # declaration order
    assert rosetta[-1] == "aResult"  # A (first) won
    assert "bResult" not in rosetta  # B's effect did NOT run
    assert "aResult" in quake
    assert not any("bResult" in c for c in quake)


@pytest.mark.lf
def test_three_way_first_declared_wins(tmp_path: Path) -> None:
    rosetta = run_machine(
        tmp_path, MODEL_DIR, "Rtc::ThreeWay", drivers=BOTH_AB, timeout="3 sec"
    )
    # A and B at one tag; A declared first -> ra; neither rb nor rt.
    assert rosetta[-1] == "ra"
    assert "rb" not in rosetta and "rt" not in rosetta


@pytest.mark.lf
def test_guard_false_yields_to_next(tmp_path: Path) -> None:
    rosetta = run_machine(
        tmp_path, MODEL_DIR, "Rtc::GuardFalse", drivers=BOTH_AB
    )
    quake = _sismic_config("Rtc::GuardFalse", ["A", "B"])
    # A is first but allowA is false -> B wins in both backends.
    assert rosetta[-1] == "rb"
    assert "ra" not in rosetta
    assert "rb" in quake
    assert not any("::ra" in c or c == "ra" for c in quake)


@pytest.mark.lf
def test_signal_beats_after_when_declared_first(tmp_path: Path) -> None:
    rosetta = run_machine(
        tmp_path,
        MODEL_DIR,
        "Rtc::SignalThenAfter",
        drivers=A_AT_1S,
        timeout="3 sec",
    )
    assert rosetta[-1] == "sigWon"  # signal declared first wins
    assert "timeoutWon" not in rosetta  # timeout effect did NOT run


@pytest.mark.lf
def test_after_beats_signal_when_declared_first(tmp_path: Path) -> None:
    rosetta = run_machine(
        tmp_path,
        MODEL_DIR,
        "Rtc::AfterThenSignal",
        drivers=A_AT_1S,
        timeout="3 sec",
    )
    assert rosetta[-1] == "timeoutWon"  # after declared first wins
    assert "sigWon" not in rosetta  # signal effect did NOT run


EV_AT_100MS = (
    "  timer fire(100 msec)\n"
    "  reaction(fire) -> m.Ev {=\n"
    "    m.Ev.set(True)\n"
    "  =}"
)


def test_compose_1a_1b_structural() -> None:
    lf = to_lf(build_program(load_model(MODEL_DIR), "Rtc::Compose1A1B"))
    # `outer` is multi-trigger (Ev interrupt + after) -> gains a fired flag.
    assert "state outer_fired = {= False =}" in lf
    # The interrupt reaction keeps the 1A consumed guard AND the 1B fired wrap.
    assert "c_outer.Ev_consumed" in lf
    assert "if not self.outer_fired:" in lf


@pytest.mark.lf
def test_compose_1a_1b_inner_first_preserved(tmp_path: Path) -> None:
    states = run_machine(
        tmp_path,
        MODEL_DIR,
        "Rtc::Compose1A1B",
        drivers=EV_AT_100MS,
        timeout="1 sec",
    )
    quake = _sismic_config("Rtc::Compose1A1B", ["Ev"])
    # Inner-first: ends in outer.innerB; the outer interrupt is suppressed.
    assert states[-1] == "outer.innerB"
    assert "aborted" not in states
    assert "outer::innerB" in quake
    assert not any("aborted" in c for c in quake)
