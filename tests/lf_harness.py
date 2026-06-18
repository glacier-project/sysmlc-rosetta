"""Shared helpers for the LF-compiling rosetta tests.

Renders a machine/rig/part with the rosetta backend, writes the generated
``.lf`` (plus any companion modules) into a temp ``src/`` tree, compiles it
with ``lfc``, and runs the produced binary.  Imported by the tests under
``lf/`` and by the inline ``lf``-marked tests in ``test_rtc.py`` /
``test_inner_first.py``.  Not collected by pytest (no ``test_`` prefix).
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from typing import TYPE_CHECKING

from sysmlc.backends.rosetta.backend import RosettaBackend
from sysmlc.backends.rosetta.builder import OUTPUT_PORT, build_program
from sysmlc.backends.rosetta.parts import build_part_program
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.sysml.loading import load_model
from sysmlc.values import configure_model

if TYPE_CHECKING:
    from pathlib import Path

    from sysmlc.values import ValueNode

HARNESS = """target Python {{
  fast: true,
  timeout: {timeout}
}}

import {reactor} from "{machine}.lf"

main reactor {{
  m = new {reactor}()
{drivers}
  reaction(m.current_state) {{=
    print(f"STATE: {{m.current_state.value}}")
  =}}
}}
"""


def compile_harness(
    tmp_path: Path,
    model_dir: Path,
    qn: str,
    *,
    timeout: str = "2 sec",
    drivers: str = "",
    values: dict[str, ValueNode] | None = None,
    python_file: Path | None = None,
) -> Path:
    """Generate and lfc-compile one machine; return the harness binary."""
    name = qn.split("::")[-1]
    model = load_model(model_dir)
    if values:
        model = configure_model(model, qn, values)
    src = tmp_path / "src"
    src.mkdir()
    external = None
    if python_file is not None:
        names = frozenset(
            n.name
            for n in ast.parse(python_file.read_text()).body
            if isinstance(n, ast.FunctionDef)
        )
        external = (python_file.stem, names)
        shutil.copy(python_file, src / python_file.name)
    program = build_program(model, qn, external=external)
    (src / f"{name}.lf").write_text(to_lf(program))
    if program.types_module_lines:
        assert program.types_module_name is not None
        (src / f"{program.types_module_name}.py").write_text(
            "\n".join(program.types_module_lines) + "\n"
        )
    (src / "Harness.lf").write_text(
        HARNESS.format(
            reactor=name, machine=name, timeout=timeout, drivers=drivers
        )
    )
    compile_result = subprocess.run(
        ["lfc", str(src / "Harness.lf")],
        capture_output=True,
        timeout=600,
    )
    assert compile_result.returncode == 0, compile_result.stderr.decode(
        errors="replace"
    )
    return tmp_path / "bin" / "Harness"


def run_machine(
    tmp_path: Path,
    model_dir: Path,
    qn: str,
    *,
    timeout: str = "2 sec",
    drivers: str = "",
    values: dict[str, ValueNode] | None = None,
    python_file: Path | None = None,
) -> list[str]:
    """Generate, compile, and run one machine; return the state sequence."""
    binary = compile_harness(
        tmp_path,
        model_dir,
        qn,
        timeout=timeout,
        drivers=drivers,
        values=values,
        python_file=python_file,
    )
    env = {**os.environ, "PYTHONPATH": str(binary.parent.parent / "src")}
    result = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return [
        line.removeprefix("STATE: ")
        for line in result.stdout.splitlines()
        if line.startswith("STATE: ")
    ]


RIG_HARNESS = """target Python {{
  fast: true,
  timeout: {timeout}
}}

import {reactor} from "{machine}.lf"

main reactor {{
  m = new {reactor}()
{observers}
}}
"""

OBSERVER = """  reaction(m.{port}) {{=
    print(f"STATE {label}: {{m.{port}.value}}")
  =}}"""


def run_rig(
    tmp_path: Path,
    model_dir: Path,
    rig_qn: str,
    *,
    timeout: str = "5 sec",
    values: dict[str, dict[str, ValueNode]] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[str]]]:
    """Build, compile, and run a rig; return (process, states per stream).

    ``values`` maps a machine's qualified name to its overrides.
    """
    name = rig_qn.split("::")[-1]
    model = load_model(model_dir)
    if values:
        for machine_qn, overrides in values.items():
            model = configure_model(model, machine_qn, overrides)
    program = RosettaBackend().build_composition(model, rig_qn)
    streams = [
        port.removesuffix(f"_{OUTPUT_PORT}") for port in program.reactor.outputs
    ]
    src = tmp_path / "src"
    src.mkdir()
    (src / f"{name}.lf").write_text(to_lf(program))
    if program.types_module_lines:
        assert program.types_module_name is not None
        (src / f"{program.types_module_name}.py").write_text(
            "\n".join(program.types_module_lines) + "\n"
        )
    observers = "\n".join(
        OBSERVER.format(port=f"{label}_current_state", label=label)
        for label in streams
    )
    (src / "Harness.lf").write_text(
        RIG_HARNESS.format(
            reactor=name,
            machine=name,
            timeout=timeout,
            observers=observers,
        )
    )
    compile_result = subprocess.run(
        ["lfc", str(src / "Harness.lf")], capture_output=True, timeout=600
    )
    assert compile_result.returncode == 0, compile_result.stderr.decode(
        errors="replace"
    )
    process = subprocess.run(
        [str(tmp_path / "bin" / "Harness")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    states: dict[str, list[str]] = {label: [] for label in streams}
    for line in process.stdout.splitlines():
        for label in streams:
            prefix = f"STATE {label}: "
            if line.startswith(prefix):
                states[label].append(line.removeprefix(prefix))
    return process, states


def run_part(
    tmp_path: Path,
    model_dir: Path,
    usage_qn: str,
    *,
    timeout: str = "10 sec",
    python_file: Path | None = None,
) -> tuple[str, int]:
    """Build, compile, and run a part system; return (debug logs, returncode).

    The generated program has its own ``main reactor``, so it compiles
    directly (written as ``Main.lf`` -- no reactor is named ``Main`` -- to
    dodge the filename/reactor-name collision). The program stays silent by
    default; a ``sitecustomize.py`` on ``PYTHONPATH`` enables DEBUG logging so
    the entry/exit observation lines reach stderr, modelling how the run (not
    the program) turns observation on.

    Args:
        tmp_path: Temporary directory for build artefacts.
        model_dir: Directory containing the SysML model to build.
        usage_qn: Qualified name of the top-level part usage.
        timeout: LF run timeout string (e.g. ``"10 sec"``).
        python_file: Optional Python module whose top-level functions back
            external calc-def calls.  When given, the module is copied into
            ``src/`` so the compiled binary can import it via PYTHONPATH.
    """
    model = load_model(model_dir)
    external = None
    if python_file is not None:
        names = frozenset(
            n.name
            for n in ast.parse(python_file.read_text()).body
            if isinstance(n, ast.FunctionDef)
        )
        external = (python_file.stem, names)
    program = build_part_program(
        model,
        usage_qn,
        target_options=(("fast", "true"), ("timeout", timeout)),
        external=external,
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "Main.lf").write_text(to_lf(program))
    if program.types_module_lines:
        assert program.types_module_name is not None
        (src / f"{program.types_module_name}.py").write_text(
            "\n".join(program.types_module_lines) + "\n"
        )
    (src / "sitecustomize.py").write_text(
        "import logging\nlogging.basicConfig(level=logging.DEBUG)\n"
    )
    if python_file is not None:
        shutil.copy(python_file, src / python_file.name)
    compile_result = subprocess.run(
        ["lfc", str(src / "Main.lf")], capture_output=True, timeout=600
    )
    assert compile_result.returncode == 0, compile_result.stderr.decode(
        errors="replace"
    )
    env = {**os.environ, "PYTHONPATH": str(src)}
    process = subprocess.run(
        [str(tmp_path / "bin" / "Main")],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    return process.stderr, process.returncode
