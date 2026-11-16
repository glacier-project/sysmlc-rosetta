"""Compile and run fixed-width C: casts, comparisons, literals and helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from sysmlc.backends import OutputOptions
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.backend import RosettaBackend
from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.program import Reaction
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.uc import UcTarget
from tests.conftest import FIXTURES_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from sysmlc_rosetta.program import LfProgram

pytestmark = pytest.mark.lf

_OBSERVED = (
    "product",
    "less",
    "top",
    "largest",
    "smallest",
    "magnitude",
    "mixed",
    "low",
    "doubled",
    "f",
)


def _observed(target: CTarget) -> LfProgram:
    """Build ``FixedWidthC::Run``; print its values at shutdown."""
    program = build_program(
        load_model(FIXTURES_DIR / "fixed-width"),
        "FixedWidthC::Run",
        target=target,
    )
    types = {var.name: var.type for var in program.reactor.state_vars}
    observer = Reaction(
        triggers=("shutdown",),
        body=target.render(
            tuple(target.observe_value(name, types[name]) for name in _OBSERVED)
        ),
    )
    root = replace(
        program.reactor, reactions=(*program.reactor.reactions, observer)
    )
    return replace(
        program,
        reactors=(*program.reactors[:-1], root),
        target_options=(
            *program.target_options,
            ("fast", "true"),
            ("timeout", "1 sec"),
        ),
    )


def _build(make: type[CTarget], out: Path) -> Path:
    """Write, compile and return the binary of the observed program."""
    target = make()
    program = _observed(target)
    backend = RosettaBackend().configured({"target": target.name})
    # reactor-uc builds through the CMakeLists.txt written beside the
    # program; lfc builds a C program from a ``src`` directory.
    src = out if make is UcTarget else out / "src"
    main = backend.write(program, OutputOptions(src, basename="Main"))[0]
    steps = (
        (
            ["cmake", "-S", str(out), "-B", str(out / "build")],
            ["cmake", "--build", str(out / "build")],
        )
        if make is UcTarget
        else (["lfc", str(main)],)
    )
    for args in steps:
        done = subprocess.run(
            args, capture_output=True, text=True, timeout=600, check=False
        )
        assert done.returncode == 0, done.stdout + done.stderr
    return out / "bin" / main.stem


@pytest.mark.parametrize(
    "make",
    [
        CTarget,
        pytest.param(
            UcTarget,
            marks=[
                pytest.mark.skipif(
                    "REACTOR_UC_PATH" not in os.environ,
                    reason="set REACTOR_UC_PATH to a reactor-uc checkout",
                ),
                pytest.mark.skipif(
                    shutil.which("cmake") is None, reason="needs cmake"
                ),
            ],
        ),
    ],
)
def test_fixed_width_values_compute_exactly(
    make: type[CTarget], tmp_path: Path
) -> None:
    binary = _build(make, tmp_path)
    done = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr
    values = dict(
        line.split(" ", 2)[1:]
        for line in done.stdout.splitlines()
        if line.startswith("__SYSMLC_VALUE__ ")
    )
    assert values == {
        # int32 maxima multiplied in int64_t, not int.
        "product": str((2**31 - 1) ** 2),
        # int32(-1) < uint32(1), compared in int64_t, not as unsigned.
        "less": "true",
        "top": str(2**64 - 1),
        # 2^53 + 1: fmax on doubles would round it to 2^53.
        "largest": str(2**53 + 1),
        "smallest": "-1",
        "magnitude": "1",
        # max(uint32 1, int32 -1) in int64_t: as uint32_t, -1 would win.
        "mixed": "1",
        "low": str(-(2**63)),
        # int32 max times -2, computed in int64_t.
        "doubled": str(-2 * (2**31 - 1)),
        "f": "0.75",
    }
