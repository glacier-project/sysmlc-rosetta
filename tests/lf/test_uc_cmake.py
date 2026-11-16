"""The reactor-uc target's CMake build: configure, build and run programs."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from sysmlc.backends import OutputOptions
from sysmlc.sysml.foreign_artifact.base import ForeignArtifact
from sysmlc.sysml.loading import load_model
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.backend import RosettaBackend
from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.targets.uc import UcTarget
from tests.conftest import FIXTURES_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from sysmlc_rosetta.program import LfProgram

pytestmark = [
    pytest.mark.lf,
    pytest.mark.skipif(
        "REACTOR_UC_PATH" not in os.environ,
        reason="set REACTOR_UC_PATH to a reactor-uc checkout",
    ),
    pytest.mark.skipif(shutil.which("cmake") is None, reason="needs cmake"),
]

_MAIN = "Main"


_REPORT = (
    "  reaction(m.current_state) {=\n"
    '    printf("state=%s\\n", m.current_state->value);\n'
    "  =}\n"
)


def _build_and_run(
    program: LfProgram, out: Path, basename: str | None = _MAIN
) -> subprocess.CompletedProcess:
    """Write ``program`` for uC, build it with CMake and run its binary.

    The main reactor gets a reaction printing each ``current_state``.
    """
    backend = RosettaBackend().configured({"target": "uc"})
    written = backend.write(program, OutputOptions(out, basename=basename))
    ulf = next(path for path in written if path.suffix == ".ulf")
    text = ulf.read_text()
    assert text.endswith("}\n")
    ulf.write_text(text[:-2] + _REPORT + "}\n")
    name = ulf.stem
    for args in (
        ["cmake", "-S", str(out), "-B", str(out / "build")],
        ["cmake", "--build", str(out / "build")],
    ):
        done = subprocess.run(
            args, capture_output=True, text=True, timeout=600, check=False
        )
        assert done.returncode == 0, done.stdout + done.stderr
    return subprocess.run(
        [str(out / "bin" / name)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_trigonometry_builds_and_runs_on_uc(tmp_path: Path) -> None:
    program = build_program(
        load_model(FIXTURES_DIR / "functions"),
        "Functions::Machine",
        target=UcTarget(),
    )

    done = _build_and_run(program, tmp_path)

    assert done.returncode == 0, done.stdout + done.stderr
    # idle -> running needs abs(-2) > 1 and cos(-2) < 1, computed through libm
    assert done.stdout.split() == ["state=idle", "state=running"]


def test_default_basename_builds_and_runs_on_uc(tmp_path: Path) -> None:
    program = build_program(
        load_model(FIXTURES_DIR / "functions"),
        "Functions::Machine",
        target=UcTarget(),
    )

    done = _build_and_run(program, tmp_path, basename=None)

    assert (tmp_path / "bin" / "MachineMain").is_file()
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.split()[-1] == "state=running"


def test_external_c_source_builds_and_runs_on_uc(tmp_path: Path) -> None:
    example = SM_EXAMPLES_DIR / "sm15-external"
    program = build_program(
        load_model(example),
        "SM15::Ramp",
        target=UcTarget(),
        external=[
            ForeignArtifact(example / "ramp.c", "c"),
            ForeignArtifact(example / "ramp.h", "c_h"),
        ],
    )
    program = replace(
        program,
        target_options=(("fast", "true"), ("timeout", "1 sec")),
    )

    done = _build_and_run(program, tmp_path)

    assert done.returncode == 0, done.stdout + done.stderr
    # each 0.1 s transition runs the external ramp.c's step()
    assert done.stdout.split()[0] == "state=run"
    assert done.stdout.count("state=run") >= 2
