"""Fixed-width features map to C types, zeros, print formats and an assert."""

import pytest
from sysmlc.sysml.loading import load_model
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.c_types import CTypes
from sysmlc_rosetta.program import LfProgram
from sysmlc_rosetta.serialize import to_lf
from sysmlc_rosetta.targets.base import LfTarget
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.uc import UcTarget
from tests.conftest import FIXTURES_DIR


def _program(qn: str, target: LfTarget | None = None) -> LfProgram:
    model = load_model(FIXTURES_DIR / "fixed-width")
    return build_program(model, qn, target=target or CTarget())


def _header(target: CTarget, name: str = "FixedWidth") -> str:
    return "\n".join(target.types.header_lines(f"{name}_H".upper()))


@pytest.mark.parametrize("make", [CTarget, UcTarget])
def test_state_variables_use_fixed_width_types(make: type) -> None:
    source = to_lf(_program("FixedWidth::Machine", make()))
    for decl in (
        "state count: uint8_t",
        "state z: int64_t",
        "state f: float",
        "state d: double",
        "state p: int32_t",
        "state x: int32_t",
    ):
        assert decl in source, decl


def test_payload_field_uses_fixed_width_type() -> None:
    target = CTarget()
    model = load_model(FIXTURES_DIR / "fixed-width")
    build_program(model, "FixedWidth::Sender", target=target)
    assert "uint16_t raw;" in _header(target)


def test_zero_of_fixed_width_types() -> None:
    types = CTypes()
    for ctype in (
        "int8_t", "int16_t", "int32_t", "int64_t", "uint8_t", "uint16_t",
        "uint32_t", "uint64_t", "float", "double",
    ):  # fmt: skip
        assert types.zero(ctype) == "0"


def test_print_formats() -> None:
    types = CTypes()
    for ctype in ("int8_t", "int16_t", "int64_t"):
        fmt, arg = types.print_format(ctype)
        assert (fmt, arg("v")) == ("%lld", "(long long)(v)")
    for ctype in ("uint8_t", "uint16_t", "uint32_t", "uint64_t"):
        fmt, arg = types.print_format(ctype)
        assert (fmt, arg("v")) == ("%llu", "(unsigned long long)(v)")
    fmt, arg = types.print_format("float")
    assert (fmt, arg("v")) == ("%.17g", "(double)(v)")
    fmt, arg = types.print_format("int32_t")
    assert (fmt, arg("v")) == ("%ld", "(long)(v)")


def test_int_width_static_assert() -> None:
    target = CTarget()
    model = load_model(FIXTURES_DIR / "fixed-width")
    build_program(model, "FixedWidth::Machine", target=target)
    assert "_Static_assert(sizeof(int) == 4," in _header(target)

    plain = CTarget()
    build_program(
        load_model(SM_EXAMPLES_DIR / "sm01-helloworld"),
        "SM01::Machine",
        target=plain,
    )
    assert "_Static_assert" not in _header(plain, "sm01")
