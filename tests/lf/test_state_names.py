from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sysmlc.backends.base import OutputOptions
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.backend import RosettaBackend
from sysmlc_rosetta.program import LfProgram
from sysmlc_rosetta.serialize import to_lf
from tests.conftest import FIXTURES_DIR
from tests.test_lf_harness import run_machine

pytestmark = pytest.mark.lf


@pytest.mark.parametrize("target", ["python", "c", "uc"])
@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("Flat", ["S1.1", "S1.2"]),
        ("CKeywords", ["idle", "_Bool", "EOF"]),
        (
            "LeadingPunctuation",
            [".Outer.Inner.1", ".Outer.Inner.2", ".Outer.done", "Finished.1"],
        ),
        ("PriorityCollision", ["S1.1", "S1_1", "S1_1_01"]),
        ("GeneratedTimerCollision", ["S1.1", "S1", "finished"]),
        ("InternalCollision", ["Outer.Inner", "Outer.done", "Finished"]),
        ("ExitCollision", ["Outer.exit_0", "Finished"]),
        ("ExitRoleCollision", ["Outer.exit.0", "Finished"]),
        ("LocalCollision", ["Outer.Inner", "_value"]),
        ("TimerCollision", ["S1", "t.S1"]),
        ("AttributeCollision", ["S1.1"]),
        (
            "EscapedLabels",
            ['Out"er.a\\b', "Out\"er.it's", 'Out"er.done', "finished"],
        ),
        ("TypesHeader", ["types.idle", "types.done", "finished"]),
        ("UcFields", ["t", "fired", "finished"]),
        ("UcAttributeTimer", ["idle", "finished"]),
        (
            "Nested",
            [
                "Outer.1.Inner.1",
                "Outer.1.Inner.2",
                "Outer.1.done",
                "Finished.1",
            ],
        ),
    ],
)
def test_dotted_states_compile_and_execute(
    tmp_path: Path, target: str, machine: str, expected: list[str]
) -> None:
    compiler = "lfc"
    if target == "uc":
        runtime = os.environ.get("REACTOR_UC_PATH")
        if runtime is None:
            pytest.skip("set REACTOR_UC_PATH to a built reactor-uc checkout")
        compiler = str(Path(runtime) / "ulf/build/install/lf-cli/bin/lfc")
        assert Path(compiler).is_file(), f"build the uC compiler: {compiler}"
    directory = FIXTURES_DIR / "state-names"
    qn = f"StateNames::{machine}"
    drivers = ""
    if machine == "LocalCollision":
        send = (
            "m.Tick.set(True)"
            if target == "python"
            else "lf_set(m.Tick, (true));"
        )
        drivers = (
            "  timer fire(2 msec)\n"
            "  reaction(fire) -> m.Tick {=\n"
            f"    {send}\n"
            "  =}\n"
        )
    if target == "python":
        states = run_machine(
            tmp_path, directory, qn, timeout="10 msec", drivers=drivers
        )
    else:
        backend = RosettaBackend(target=target)
        program = backend.build(load_model(directory), qn)
        src = tmp_path / "src"
        src.mkdir()
        backend.write(program, OutputOptions(output_dir=src))
        extension = "ulf" if target == "uc" else "lf"
        header = (
            '@platform("native")\n@fast(true)\n@timeout(10 msec)\n'
            '@logging("ERROR")\n'
            if target == "uc"
            else ""
        )
        value = (
            "*lf_get(m.current_state)"
            if target == "uc"
            else "m.current_state->value"
        )
        harness = src / f"Harness.{extension}"
        harness.write_text(
            (
                "target C { fast: true, timeout: 10 msec }\n"
                if target == "c"
                else ""
            )
            + f'import {machine} from "{machine}.{extension}"\n'
            + header
            + "main reactor {\n"
            f"  m = new {machine}()\n"
            f"{drivers}"
            "  reaction(m.current_state) {=\n"
            f'    printf("STATE: %s\\n", {value});\n'
            "  =}\n"
            "}\n"
        )
        compiled = subprocess.run(
            [compiler, str(harness)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        process = subprocess.run(
            [str(tmp_path / "bin" / "Harness")],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        states = [
            line.removeprefix("STATE: ")
            for line in process.stdout.splitlines()
            if line.startswith("STATE: ")
        ]
    # A reactor port exposes the final value at each tag: initial child
    # entry replaces its parent's announcement at the same tag.
    assert states == expected


@pytest.mark.parametrize("target", ["python", "c"])
@pytest.mark.parametrize("machine", ["CKeywords", "LeadingPunctuation"])
def test_generated_program_validates_directly(
    tmp_path: Path, target: str, machine: str
) -> None:
    backend = RosettaBackend(target=target)
    program = backend.build(
        load_model(FIXTURES_DIR / "state-names"), f"StateNames::{machine}"
    )
    assert isinstance(program, LfProgram)
    src = tmp_path / "src"
    src.mkdir()
    backend.write(program, OutputOptions(output_dir=src))
    source = src / "Standalone.lf"
    source.write_text(to_lf(program))
    result = subprocess.run(
        ["lfc", "--no-compile", str(source)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
