"""Generated C for fixed-width features: casts, literals and integer calls."""

from __future__ import annotations

import logging
import re

import pytest
import syside
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.fixed_width import FIXED_WIDTH
from sysmlc_rosetta.serialize import to_lf
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.python import PythonTarget
from sysmlc_rosetta.targets.uc import UcTarget
from sysmlc_rosetta.width import WidthAnalysis
from tests.conftest import FIXTURES_DIR

_C_TARGETS = pytest.mark.parametrize("make", [CTarget, UcTarget])


@pytest.fixture(scope="module")
def model() -> syside.Model:
    return load_model(FIXTURES_DIR / "fixed-width")


def _generated(model: syside.Model, make: type[CTarget]) -> tuple[str, str]:
    """The program and types header of ``FixedWidthC::Casts``."""
    target = make()
    source = to_lf(build_program(model, "FixedWidthC::Casts", target=target))
    header = "\n".join(target.types.header_lines("H"))
    return source, header


def _statement(source: str, target: str, value: str) -> None:
    assert f"self->{target} = {value};" in source, source


@_C_TARGETS
def test_widening_cast_on_store(model: syside.Model, make: type) -> None:
    source, _ = _generated(model, make)
    _statement(source, "z", "(int64_t)(self->x) * (int64_t)(self->y)")


@_C_TARGETS
def test_widening_cast_uint32(model: syside.Model, make: type) -> None:
    source, _ = _generated(model, make)
    _statement(source, "big", "(uint32_t)(self->a) * (uint32_t)(self->b)")


@_C_TARGETS
def test_no_cast_for_narrow_destination(
    model: syside.Model, make: type
) -> None:
    source, _ = _generated(model, make)
    _statement(source, "count", "self->count + 1")


@_C_TARGETS
def test_no_cast_inside_call_arguments(model: syside.Model, make: type) -> None:
    source, _ = _generated(model, make)
    _statement(source, "z", "sysmlc_max_int32(self->x * self->y, 0)")


@_C_TARGETS
def test_mixed_sign_comparison_widened(
    model: syside.Model, make: type, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="sysmlc_rosetta.width"):
        source, _ = _generated(model, make)
    _statement(source, "flag", "(int64_t)(self->s32) < (int64_t)(self->u32)")
    # No wider type than uint64_t: the C is unchanged and rosetta warns.
    _statement(source, "flag", "self->u64 > self->s32")
    assert any(
        "u64 > s32 compares uint64_t with a possibly negative int32_t"
        in record.getMessage()
        for record in caplog.records
    )


@_C_TARGETS
def test_large_literals(model: syside.Model, make: type) -> None:
    source, _ = _generated(model, make)
    _statement(source, "u64", "UINT64_C(18446744073709551615)")
    _statement(source, "l", "9223372036854775807")
    # A negated literal is one signed literal: INT64_MIN, not a negated
    # UINT64_C, and a signed cast leaf.
    _statement(source, "l", "INT64_MIN")
    _statement(source, "l", "(int64_t)(self->x) * (int64_t)(-2)")


def test_negated_literal_kind_and_warnings(model: syside.Model) -> None:
    analysis = WidthAnalysis()
    for assign in model.nodes(syside.AssignmentActionUsage):
        value = assign.value_expression
        if value is not None and syside.pprint(value).strip() == (
            "-9223372036854775808"
        ):
            evaluation = analysis.check(value)
            assert evaluation.kind(value) == FIXED_WIDTH["int64"]
            assert evaluation.warnings == ()


@pytest.mark.parametrize("make", [CTarget, UcTarget, PythonTarget])
def test_literal_above_uint64_max_is_rejected(make: type) -> None:
    # Syside stores it as UINT64_MAX: no target could emit its value.
    with pytest.raises(UnsupportedConstructError, match="64 bits"):
        build_program(_overflow(), "TooLarge::Machine", target=make())


@_C_TARGETS
def test_literal_below_int64_min_is_rejected_in_c(make: type) -> None:
    with pytest.raises(UnsupportedConstructError, match="64 bits"):
        build_program(_overflow(), "TooLarge::Negative", target=make())


def test_literal_below_int64_min_builds_on_python_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="sysmlc_rosetta.width"):
        program = build_program(
            _overflow(), "TooLarge::Negative", target=PythonTarget()
        )
    source = to_lf(program)
    assert "self.l = -9223372036854775809" in source
    assert "self.p = -9223372036854775809" in source
    # The int64 store warns with the true range; the plain Integer one not.
    (warning,) = [
        record.getMessage()
        for record in caplog.records
        if record.name == "sysmlc_rosetta.width"
    ]
    assert (
        "TooLarge::Negative::l: -9223372036854775809 needs "
        "-9223372036854775809..-9223372036854775809, stored in int64"
    ) in warning


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("boxed", syside.ConstructorExpression),
        ("chained", syside.FeatureChainExpression),
    ],
)
def test_literal_check_reaches_every_node_kind(name: str, kind: type) -> None:
    (feature,) = (
        feature
        for feature in _overflow().nodes(syside.AttributeUsage)
        if feature.name == name
    )
    value = feature.feature_value_expression
    assert value is not None
    assert isinstance(value, kind)
    with pytest.raises(UnsupportedConstructError, match="64 bits"):
        WidthAnalysis().check(value)


def _overflow() -> syside.Model:
    return load_model(FIXTURES_DIR / "fixed-width-overflow")


@_C_TARGETS
def test_integer_min_max_abs(model: syside.Model, make: type) -> None:
    source, header = _generated(model, make)
    _statement(source, "l", "sysmlc_max_int64(self->l, self->m)")
    _statement(source, "m", "sysmlc_min_int64(self->l, 3)")
    _statement(source, "l", "sysmlc_abs_int64(self->m)")
    # Each argument is a function argument: evaluated once.
    for helper in (
        "static inline int64_t sysmlc_max_int64(int64_t a, int64_t b) "
        "{ return a > b ? a : b; }",
        "static inline int64_t sysmlc_min_int64(int64_t a, int64_t b) "
        "{ return a < b ? a : b; }",
        "static inline int64_t sysmlc_abs_int64(int64_t a) "
        "{ return a < 0 ? -a : a; }",
        "static inline int32_t sysmlc_max_int32(int32_t a, int32_t b) "
        "{ return a > b ? a : b; }",
    ):
        assert helper in header, header
    # Only the helpers used are declared.
    assert "sysmlc_min_int32" not in header
    # Real and plain Integer arguments keep the math library.
    _statement(source, "r", "fmax(self->r, 0.5)")
    _statement(source, "p", "fmax(self->p, 1)")
    assert not re.search(r"\b(fabs|fmin)\(", source)


@_C_TARGETS
def test_mixed_sign_min_max(
    model: syside.Model, make: type, caplog: pytest.LogCaptureFixture
) -> None:
    # C would compare -1 as uint32_t: the arguments widen to int64_t.
    with caplog.at_level(logging.WARNING, logger="sysmlc_rosetta.width"):
        source, _ = _generated(model, make)
    _statement(source, "l", "sysmlc_max_int64(self->u32, self->s32)")
    # No wider type than uint64_t: computed in it, and rosetta warns.
    _statement(source, "u64", "sysmlc_min_uint64(self->u64, self->s32)")
    assert any(
        "min(u64, s32) compares uint64_t with a possibly negative int32_t"
        in record.getMessage()
        for record in caplog.records
    )


@_C_TARGETS
def test_float32_stays_single_precision(
    model: syside.Model, make: type
) -> None:
    source, _ = _generated(model, make)
    _statement(source, "f", "self->f + 0.25f")
    _statement(source, "flag", "self->f < 0.5f")
    _statement(source, "d", "self->d + 0.25")
    _statement(source, "r", "self->r + 0.75")


@_C_TARGETS
def test_comparisons_outside_stores(model: syside.Model, make: type) -> None:
    source = to_lf(
        build_program(model, "FixedWidthC::Conditions", target=make())
    )
    widened = "(int64_t)(self->s32) < (int64_t)(self->u32)"
    # In the guard, and in the constraint's startup and per-write checks.
    assert f"if ({widened} && self->f < 0.5f)" in source, source
    assert f"if (!({widened}))" in source, source


def test_python_output_is_unchanged(model: syside.Model) -> None:
    source = to_lf(
        build_program(model, "FixedWidthC::Casts", target=PythonTarget())
    )
    for line in (
        "self.z = self.x * self.y",
        "self.flag = self.s32 < self.u32",
        "self.u64 = 18446744073709551615",
        "self.l = max(self.l, self.m)",
        "self.f = self.f + 0.25",
    ):
        assert line in source, line


@_C_TARGETS
def test_plain_model_declares_no_helpers(make: type) -> None:
    target = make()
    build_program(
        load_model(SM_EXAMPLES_DIR / "sm01-helloworld"),
        "SM01::Machine",
        target=target,
    )
    assert "static inline" not in "\n".join(target.types.header_lines("H"))
