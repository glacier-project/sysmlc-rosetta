"""Width analysis: overflow warnings for fixed-width stores and arithmetic."""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
import syside
from sysmlc.cli import main
from sysmlc.sysml.loading import load_model
from sysmlc_models._validation.rosetta import _support
from sysmlc_models.catalog import model_path
from sysmlc_models.scenarios import SCENARIOS

from sysmlc_rosetta.backend import RosettaBackend
from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.fixed_width import FIXED_WIDTH
from sysmlc_rosetta.targets import TARGETS
from sysmlc_rosetta.width import Evaluation, WidthAnalysis
from tests.conftest import FIXTURES_DIR

if TYPE_CHECKING:
    from pathlib import Path

_FIXTURE = FIXTURES_DIR / "fixed-width"
_LOGGER = "sysmlc_rosetta.width"


@pytest.fixture(scope="module")
def model() -> syside.Model:
    return load_model(_FIXTURE)


def _logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER]


def _warnings(
    caplog: pytest.LogCaptureFixture, model: syside.Model, name: str
) -> list[str]:
    """The width warnings of one fixture machine, the same on every target."""
    found: dict[str, list[str]] = {}
    for target, make in TARGETS.items():
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            build_program(model, f"Warnings::{name}", target=make())
        found[target] = _logged(caplog)
    assert found["c"] == found["python"] == found["uc"]
    return found["python"]


def _one(warnings: list[str], *parts: str) -> None:
    assert len(warnings) == 1, warnings
    for part in parts:
        assert part in warnings[0], (part, warnings[0])


def test_increment_overflows_uint8(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "Increment"),
        "Warnings::Increment::count: count + 1 needs 1..256, stored in uint8",
        "warnings.sysml:14:",
    )


def test_literal_bound_silences_store(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    assert _warnings(caplog, model, "Bounded") == []


def test_mirrored_and_conjoined_bounds_narrow(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    # 10 <= count and count < 101: count + 155 fits, count - 11 does not.
    _one(
        _warnings(caplog, model, "BoundedRange"),
        "BoundedRange::count: count - 11 needs -1..89, stored in uint8",
    )


def test_bound_against_attribute_does_not_narrow(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "BoundedByAttribute"),
        "BoundedByAttribute::count",
        "1..256",
        "stored in uint8",
    )


def test_negated_bound_does_not_narrow(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "NegatedBound"),
        "NegatedBound::count",
        "1..256",
        "stored in uint8",
    )


def test_narrow_product_is_computed_in_int(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "NarrowProductGuard"),
        "Warnings::NarrowProductGuard",
        "a * b needs 0..4294836225, computed in int32_t",
    )


def test_widening_store_does_not_warn(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    assert _warnings(caplog, model, "WideningStore") == []


def test_wide_product_in_guard_warns(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "WideProductGuard"),
        "WideProductGuard",
        f"x * y needs {-(2**31) * (2**31 - 1)}..{2**62}, computed in int32_t",
    )


def test_plain_integer_store_does_not_warn(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    assert _warnings(caplog, model, "PlainIntegerStore") == []


def test_signed_into_unsigned_warns(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "SignedIntoUnsigned"),
        "SignedIntoUnsigned::u: s needs -128..127, stored in uint8",
    )


def test_float64_into_float32_warns(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "DoubleIntoFloat"),
        "DoubleIntoFloat::f: d is float64, stored in float32",
    )


def test_float32_stores_do_not_warn(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    assert _warnings(caplog, model, "FloatStores") == []


def test_real_into_integer_warns(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "RealIntoInteger"),
        "RealIntoInteger::count: r is real, stored in uint8",
    )


def _values(model: syside.Model, owner: str) -> dict[str, syside.Expression]:
    """The assigned values of one fixture state def, by source text."""
    found: dict[str, syside.Expression] = {}
    for assign in model.nodes(syside.AssignmentActionUsage):
        value = assign.value_expression
        target = assign.referent
        if value is None or target is None or target.owner is None:
            continue
        if str(target.owner.qualified_name) == f"Warnings::{owner}":
            found[syside.pprint(value).strip()] = value
    return found


def _typing(model: syside.Model, source: str) -> Evaluation:
    return WidthAnalysis().check(_values(model, "Typing")[source])


def test_modulo_with_literal_divisor_fits(model: syside.Model) -> None:
    # The code generators do not render `%`, so analyse the store directly.
    wrapped = _values(model, "Wrapping")["(count + 1) % 256"]
    assert isinstance(wrapped, syside.OperatorExpression)
    uint8 = FIXED_WIDTH["uint8"]
    analysis = WidthAnalysis()
    assert analysis.evaluate(wrapped, uint8, "w").warnings == ()
    (dividend, _) = wrapped.operands.collect()
    assert analysis.evaluate(dividend, uint8, "w").warnings != ()


def test_cast_store_warns_once(model: syside.Model) -> None:
    # Rule 2 at the root of a cast store says what rule 1 says.
    (warning,) = _typing(model, "c + 1").warnings
    assert (
        "Typing::c: c + 1 needs -2147483647..2147483648, stored in int32"
        in (warning)
    )


def test_store_cast_never_narrows(model: syside.Model) -> None:
    value = _values(model, "Typing")["u - 2147483648"]
    evaluation = WidthAnalysis().check(value)
    assert evaluation.warnings == ()
    assert evaluation.cast_leaves == frozenset()
    # uint32 against the literal's long long: computed in int64_t.
    assert evaluation.kind(value) == FIXED_WIDTH["int64"]


def test_wider_leaf_keeps_its_type(model: syside.Model) -> None:
    value = _values(model, "Typing")["l"]
    evaluation = WidthAnalysis().check(value)
    assert evaluation.cast_leaves == frozenset()
    assert evaluation.kind(value) == FIXED_WIDTH["int64"]
    _one(list(evaluation.warnings), "Typing::i: l needs", "stored in int32")


def test_cast_leaves_of_a_widening_store(model: syside.Model) -> None:
    value = _values(model, "Typing")["x * y"]
    assert isinstance(value, syside.OperatorExpression)
    evaluation = WidthAnalysis().check(value)
    assert evaluation.cast_leaves == frozenset(value.operands.collect())
    int64 = FIXED_WIDTH["int64"]
    assert {evaluation.kind(n) for n in (value, *value.operands.collect())} == {
        int64
    }
    assert evaluation.warnings == ()


def test_call_stops_the_cast_and_its_argument_is_checked(
    model: syside.Model,
) -> None:
    evaluation = _typing(model, "NumericalFunctions::max(x * y, 0)")
    assert evaluation.cast_leaves == frozenset()
    _one(list(evaluation.warnings), "x * y needs", "computed in int32_t")


def test_overflow_carried_in_the_same_type_warns_once(
    model: syside.Model,
) -> None:
    evaluation = _typing(model, "x * y * l > 0")
    assert [w.split(" (")[0] for w in evaluation.warnings] == [
        f"Warnings::Typing::flag: x * y needs {-(2**31) * (2**31 - 1)}.."
        f"{2**62}, computed in int32_t",
        f"Warnings::Typing::flag: x * y * l needs {-(2**125)}.."
        f"{2**62 * (2**63 - 1)}, computed in int64_t",
    ]


def test_uint32_comparison_is_widened(model: syside.Model) -> None:
    value = _values(model, "Typing")["x < u"]
    evaluation = WidthAnalysis().check(value)
    assert evaluation.widened == {value}
    assert evaluation.warnings == ()


def test_uint64_comparison_with_signed_warns(model: syside.Model) -> None:
    evaluation = _typing(model, "u64 > x")
    assert evaluation.widened == frozenset()
    _one(
        list(evaluation.warnings),
        "Typing::flag: u64 > x compares uint64_t with a possibly negative int32_t",
    )
    assert _typing(model, "u64 > 0").warnings == ()


def test_single_precision_literals(model: syside.Model) -> None:
    def single(source: str) -> list[str]:
        evaluation = _typing(model, source)
        return sorted(syside.pprint(n).strip() for n in evaluation.single)

    assert single("f * 2.0 + 0.25") == ["0.25", "2.0"]
    assert single("0.1") == ["0.1"]
    assert single("r + 0.5") == []
    assert single("d + 0.5") == []
    assert single("f < 0.75") == ["0.75"]
    assert single("f == 0.1") == ["0.1"]
    assert single("d < 0.75") == []
    assert single("r == 0.1") == []


def test_in_attribute_default_store_warns(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "InDefault"),
        "InDefault::limit: 300 needs 300..300, stored in uint8",
    )


def test_out_of_range_initial_value_warns(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "OutOfRangeInitial"),
        "OutOfRangeInitial::count: 256 needs 256..256, stored in uint8",
    )


def test_constraint_arithmetic_is_checked(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "ConstraintArithmetic"),
        "a * b needs 0..4294836225, computed in int32_t",
    )


def test_when_condition_arithmetic_is_checked(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "WhenArithmetic"),
        "a * b needs 0..4294836225, computed in int32_t",
    )


def test_payload_field_store_warns(
    caplog: pytest.LogCaptureFixture, model: syside.Model
) -> None:
    _one(
        _warnings(caplog, model, "PayloadStore"),
        "raw: a + b needs 0..131070, stored in uint16",
    )


def test_values_override_out_of_range_warns(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    arguments = ["rosetta", "build", str(_FIXTURE)]
    arguments += ["-e", "Warnings::Configurable", "-o", str(tmp_path)]
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert main(arguments) == 0
    assert _logged(caplog) == []
    values = tmp_path / "values.yaml"
    values.write_text("Warnings:\n  Configurable:\n    count: 300\n")
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        assert main([*arguments, "--values", str(values)]) == 0
    _one(
        _logged(caplog),
        "Warnings::Configurable::count: 300 needs 300..300, stored in uint8",
    )


def test_cli_prints_width_warnings_on_stderr(tmp_path: Path) -> None:
    # Through the CLI's logging, formatted like core's own records.
    arguments = ["rosetta", "build", str(_FIXTURE), "-e", "Warnings::Increment"]
    arguments += ["--target", "c", "-o", str(tmp_path)]
    done = subprocess.run(
        [sys.executable, "-m", "sysmlc.cli", *arguments],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert (
        f"WARNING - {_LOGGER} - Warnings::Increment::count: count + 1 needs "
        "1..256, stored in uint8"
    ) in done.stderr


def test_no_warnings_without_fixed_width(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenarios = {
        (s.model, s.element): s
        for s in SCENARIOS
        if s.kind in {"state", "part"}
    }
    models: dict[str, syside.Model] = {}
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        for (name, element), scenario in sorted(scenarios.items()):
            model = models.get(name)
            if model is None:
                model = models[name] = load_model(model_path(name))
            for target in ("python", "c"):
                backend = RosettaBackend(target)
                # sysmlc-models has no public lookup of a scenario's
                # per-target support artifacts; reuse its harness's one.
                external = _support(scenario, TARGETS[target]())
                if scenario.kind == "part":
                    backend.build_part(model, element, external=external)
                else:
                    backend.build(model, element, external=external)
    assert len(scenarios) > 50
    assert _logged(caplog) == []
