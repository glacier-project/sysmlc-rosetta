import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.parts import build_part_program
from sysmlc_rosetta.serialize import to_lf
from tests.conftest import FIXTURES_DIR

pytestmark = pytest.mark.lf


@pytest.mark.parametrize(
    "machine,send_tick,name,part",
    [
        ("DuplicateConditions", False, "firstLimit", False),
        ("Anonymous", False, None, False),
        ("Delayed", True, 'limit "quoted"', False),
        ("system", False, "firstLimit", True),
    ],
)
def test_native_assertion_reports_its_exact_source_identity(
    tmp_path: Path, machine: str, send_tick: bool, name: str | None, part: bool
) -> None:
    model = load_model(FIXTURES_DIR / "constraint-identity")
    program = (
        build_part_program(
            model,
            f"ConstraintIdentity::{machine}",
            target_options=(("fast", "true"), ("timeout", "1 sec")),
        )
        if part
        else build_program(model, f"ConstraintIdentity::{machine}")
    )
    src = tmp_path / "src"
    src.mkdir()
    if not part:
        (src / f"{machine}.lf").write_text(to_lf(program))
    if program.types_module_name is not None:
        (src / f"{program.types_module_name}.py").write_text(
            "\n".join(program.types_module_lines) + "\n"
        )
    driver = (
        "  timer tick(100 msec)\n"
        "  reaction(tick) -> m.Tick {= m.Tick.set(True) =}\n"
        if send_tick
        else ""
    )
    harness = src / "Harness.lf"
    harness.write_text(
        to_lf(program)
        if part
        else (
            "target Python { fast: true, timeout: 1 sec }\n"
            f'import {machine} from "{machine}.lf"\n'
            f"main reactor {{\n  m = new {machine}()\n{driver}}}\n"
        )
    )
    compiled = subprocess.run(
        ["lfc", str(harness)], capture_output=True, text=True, timeout=600
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    executed = subprocess.run(
        [str(tmp_path / "bin" / "Harness")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert executed.returncode != 0
    prefix = "AssertionError: SysML constraint violated: "
    payloads = [
        json.loads(line.removeprefix(prefix))
        for line in (executed.stdout + executed.stderr).splitlines()
        if line.startswith(prefix)
    ]
    assert payloads, executed.stdout + executed.stderr
    assert all(p == asdict(program.constraints[0]) for p in payloads)
    assert payloads[0]["name"] == name
    assert payloads[0]["behavior"] == (
        "ConstraintIdentity::DuplicateConditions"
        if part
        else f"ConstraintIdentity::{machine}"
    )
