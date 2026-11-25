from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sysmlc.backends.base import OutputOptions
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model
from sysmlc.values import configure_model

from sysmlc_rosetta.backend import RosettaBackend
from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.program import Instantiation, MainReactor, Reaction
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.python import PythonTarget
from sysmlc_rosetta.targets.uc import UcTarget
from tests.conftest import FIXTURES_DIR

if TYPE_CHECKING:
    from sysmlc_rosetta.program import LfProgram
    from sysmlc_rosetta.targets.base import LfTarget

FIXTURE = FIXTURES_DIR / "nested-data"


def _execute(
    tmp_path: Path, program: LfProgram, target: LfTarget
) -> subprocess.CompletedProcess[str]:
    uc = target.name == "uc"
    compiler = "lfc"
    if uc:
        runtime = os.environ.get("REACTOR_UC_PATH")
        if runtime is None:
            pytest.skip("set REACTOR_UC_PATH to a built reactor-uc checkout")
        compiler = str(Path(runtime) / "ulf/build/install/lf-cli/bin/lfc")
        assert Path(compiler).is_file(), f"build the uC compiler: {compiler}"
    types = {
        v.name: v.type
        for v in (*program.reactor.parameters, *program.reactor.state_vars)
    }
    modes = tuple(
        replace(
            mode,
            reactions=(
                replace(
                    mode.reactions[0],
                    body=(
                        *mode.reactions[0].body,
                        *target.render(
                            tuple(
                                target.observe_value(name, types[name])
                                for name in types
                                if name
                                in {
                                    "sequence",
                                    "visits",
                                    "enabled",
                                    "temperature",
                                }
                            )
                        ),
                    ),
                ),
                *mode.reactions[1:],
            ),
        )
        if mode.name == "finishedState"
        else mode
        for mode in program.reactor.modes
    )
    program = replace(
        program,
        reactors=(
            *program.reactors[:-1],
            replace(program.reactor, modes=modes),
        ),
        preamble=(*program.preamble, "import json")
        if target.name == "python"
        else program.preamble,
        target_options=(
            *program.target_options,
            ("fast", "true"),
            ("timeout", "10 msec"),
            *(() if uc else (("workers", "4"),)),
        ),
    )
    src = tmp_path if uc else tmp_path / "src"
    src.mkdir(exist_ok=True)
    backend = RosettaBackend(target=target.name)
    written = backend.write(program, OutputOptions(src, basename="Nested"))
    source = next(path for path in written if path.suffix in {".lf", ".ulf"})
    commands = (
        (
            ["cmake", "-S", str(src), "-B", str(tmp_path / "build")],
            ["cmake", "--build", str(tmp_path / "build")],
        )
        if uc
        else ([compiler, str(source)],)
    )
    for command in commands:
        compiled = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600 if uc else 120,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    return subprocess.run(
        [str(tmp_path / "bin" / "Nested")],
        capture_output=True,
        text=True,
        timeout=120 if uc else 30,
    )


def _values(output: str, name: str) -> list[int | float | bool]:
    prefix = f"__SYSMLC_VALUE__ {name} "
    return [
        json.loads(line.removeprefix(prefix))
        for line in output.splitlines()
        if line.startswith(prefix)
    ]


@pytest.mark.lf
@pytest.mark.parametrize("target_type", [PythonTarget, CTarget, UcTarget])
@pytest.mark.parametrize("digit", [1, 5])
def test_nested_data_order_reentry_and_instance_isolation(
    tmp_path: Path, target_type: type[LfTarget], digit: int
) -> None:
    target = target_type()
    model = configure_model(
        load_model(FIXTURE), "NestedData::Completion", {"digit": digit}
    )
    program = build_program(model, "NestedData::Completion", target=target)
    program = replace(
        program,
        main=MainReactor(
            instantiations=(
                Instantiation("first", "Completion"),
                Instantiation("second", "Completion"),
            )
        ),
    )
    result = _execute(tmp_path, program, target)
    assert result.returncode == 0, result.stdout + result.stderr
    first = digit * 1000 + 234
    assert sorted(_values(result.stdout, "sequence")) == [
        first,
        first,
        first * 10000 + first,
        first * 10000 + first,
    ]
    assert sorted(_values(result.stdout, "visits")) == [1, 1, 2, 2]
    assert _values(result.stdout, "enabled") == [False] * 4
    assert sorted(_values(result.stdout, "temperature")) == [
        21.5,
        21.5,
        22.0,
        22.0,
    ]


@pytest.mark.lf
@pytest.mark.parametrize("target_type", [PythonTarget, CTarget, UcTarget])
@pytest.mark.parametrize(
    ("machine", "expected"), [("ParentChange", 1234), ("TimedParent", 123456)]
)
def test_child_write_reaches_parent_selection(
    tmp_path: Path, target_type: type[LfTarget], machine: str, expected: int
) -> None:
    target = target_type()
    program = build_program(
        load_model(FIXTURE), f"NestedData::{machine}", target=target
    )
    result = _execute(tmp_path, program, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _values(result.stdout, "sequence") == [expected]


@pytest.mark.lf
@pytest.mark.parametrize("target_type", [PythonTarget, CTarget, UcTarget])
def test_child_write_checks_root_constraint(
    tmp_path: Path, target_type: type[LfTarget]
) -> None:
    target = target_type()
    program = build_program(
        load_model(FIXTURE), "NestedData::Constraint", target=target
    )
    result = _execute(tmp_path, program, target)
    assert result.returncode != 0
    assert "SysML constraint violated:" in result.stdout + result.stderr
    assert "nonnegative" in result.stdout + result.stderr


def test_parallel_shared_access_remains_diagnosed() -> None:
    with pytest.raises(
        UnsupportedConstructError, match="under a parallel state"
    ):
        build_program(load_model(FIXTURE), "NestedData::ParallelAccess")


@pytest.mark.lf
@pytest.mark.parametrize("target_type", [PythonTarget, CTarget, UcTarget])
@pytest.mark.parametrize("name", ["_data", "_data_changed"])
def test_generated_data_names_preserve_machine_updates(
    tmp_path: Path, name: str, target_type: type[LfTarget]
) -> None:
    (tmp_path / "names.sysml").write_text(f"""
        package Names {{
            private import ScalarValues::*;
            state def Machine {{
                attribute flag : Boolean := false;
                entry; then enclosing;
                state enclosing {{
                    entry; then {name};
                    state {name} {{ entry assign flag := true; }}
                }}
                state finishedState;
                transition first enclosing accept when flag then finishedState;
            }}
        }}
    """)
    target = target_type()
    program = build_program(
        load_model(tmp_path), "Names::Machine", target=target
    )
    types = {v.name: v.type for v in program.reactor.state_vars}
    observation = Reaction(
        ("shutdown",),
        (),
        target.render((target.observe_value("flag", types["flag"]),)),
    )
    program = replace(
        program,
        reactors=(
            *program.reactors[:-1],
            replace(
                program.reactor,
                reactions=(*program.reactor.reactions, observation),
            ),
        ),
    )
    result = _execute(tmp_path, program, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _values(result.stdout, "flag") == [True]
