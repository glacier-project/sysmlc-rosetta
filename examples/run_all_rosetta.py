#!/usr/bin/env python3
"""Build, compile, and run every Style-A showcase model end to end.

Each showcase model declares a single top-level part usage (e.g.
``Microwave::microwaveSystem``) that the CLI auto-selects and translates
into a self-contained ``main reactor``.  Observation is backend-generated:
``logging.debug("entered <Reactor>.<state>")`` / ``"exited ..."`` lines
reach stderr when the run enables DEBUG via a ``sitecustomize.py`` on
``PYTHONPATH``.

Pipeline (per model directory under ``models/showcase/``):

1. **Build** - ``python -m sysmlc.cli rosetta build <model_dir> -o <src>
   --fast --timeout <T>`` auto-selects the single top-level part usage.
   Models that ship exactly one ``*.py`` file receive ``--python <file>``
   so the CLI copies the module into ``src/``.
2. **Rename** - the CLI writes ``<Name>.lf`` whose anonymous main reactor
   would clash with a contained reactor of the same name.  We rename the
   single generated ``*.lf`` to ``src/Main.lf`` (``lfc`` names the
   anonymous main ``Main``, which is distinct from any contained reactor).
3. **sitecustomize** - ``src/sitecustomize.py`` sets
   ``logging.basicConfig(level=logging.DEBUG)`` so entry/exit lines reach
   stderr when the binary is run with ``PYTHONPATH=<src>``.
4. **Compile** - ``lfc src/Main.lf``.
5. **Run** - ``bin/Main`` with ``PYTHONPATH=<src>``.  Exit code 0 means
   the model's SysML testbench verdict held (or a clean timeout for
   driverless models).
6. **Trajectory** - stderr lines matching ``entered <token>`` are
   collected for the per-model report.

Usage:
    python models/showcase/run_all.py [--only thermostat microwave]
        [--timeout "60 sec"] [--show-states N]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SHOWCASE_DIR = Path(__file__).resolve().parent

_ENTERED_RE = re.compile(r"entered (\S+)")

SITECUSTOMIZE = "import logging\nlogging.basicConfig(level=logging.DEBUG)\n"


@dataclass
class Result:
    """Outcome of one model's build/compile/run pipeline."""

    model: str
    stage: str  # furthest stage reached: build | compile | run
    ok: bool
    states: list[str]
    detail: str = ""


def _model_dirs(only: list[str] | None) -> list[Path]:
    dirs = sorted(
        d
        for d in SHOWCASE_DIR.iterdir()
        if d.is_dir() and any(d.glob("*.sysml"))
    )
    if only:
        chosen = [d for d in dirs if d.name in only]
        missing = set(only) - {d.name for d in chosen}
        if missing:
            sys.exit(f"unknown model(s): {', '.join(sorted(missing))}")
        return chosen
    return dirs


def _build(model_dir: Path, src: Path, timeout: str) -> tuple[bool, str]:
    """Invoke the CLI to build one model into *src*.

    Args:
        model_dir: Directory containing the SysML model.
        src: Destination directory for generated ``.lf`` artefacts.
        timeout: LF run timeout string forwarded to ``--timeout``.

    Returns:
        ``(ok, detail)`` where *detail* is an error tail on failure or
        an empty string on success.
    """
    command = [
        sys.executable,
        "-m",
        "sysmlc.cli",
        "rosetta",
        "build",
        str(model_dir),
        "-o",
        str(src),
        "--fast",
        "--timeout",
        timeout,
    ]
    py_files = list(model_dir.glob("*.py"))
    if len(py_files) == 1:
        command += ["--python", str(py_files[0])]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        tail = completed.stderr.strip().splitlines()
        return False, tail[-1] if tail else completed.stderr.strip()
    return True, ""


def _run_model(
    model_dir: Path,
    build_root: Path,
    timeout: str,
) -> Result:
    """Build, compile, and run one showcase model.

    Args:
        model_dir: Source directory containing the SysML model.
        build_root: Root directory for build artefacts
            (``<build_root>/<model>/src`` and ``bin``).
        timeout: LF run timeout forwarded to the CLI and to ``lfc``.

    Returns:
        A :class:`Result` describing the pipeline outcome.
    """
    name = model_dir.name
    src = build_root / name / "src"
    src.mkdir(parents=True, exist_ok=True)
    # Drop stale .lf from earlier runs; the rename step expects exactly one.
    for stale in src.glob("*.lf"):
        stale.unlink()

    # Stage 1 — build
    ok, detail = _build(model_dir, src, timeout)
    if not ok:
        return Result(name, "build", False, [], detail)

    # Stage 2 — rename to Main.lf (dodges filename/reactor-name collision)
    lf_files = list(src.glob("*.lf"))
    if len(lf_files) != 1:
        return Result(
            name,
            "build",
            False,
            [],
            f"expected 1 .lf file, found {len(lf_files)}",
        )
    main_lf = src / "Main.lf"
    lf_files[0].rename(main_lf)

    # Stage 3 — sitecustomize enables DEBUG logging
    (src / "sitecustomize.py").write_text(SITECUSTOMIZE)

    # Stage 4 — compile
    compiled = subprocess.run(
        ["lfc", str(main_lf)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if compiled.returncode != 0:
        return Result(
            name, "compile", False, [], compiled.stderr.strip()[-200:]
        )

    # Stage 5 — run
    binary = build_root / name / "bin" / "Main"
    # PYTHONPATH is set only for sitecustomize.py (DEBUG logging); types and
    # physics modules are imported via files: — lfc copies them into src-gen.
    env = {**os.environ, "PYTHONPATH": str(src)}
    ran = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )

    # Stage 6 — parse trajectory from DEBUG stderr
    states = _ENTERED_RE.findall(ran.stderr)
    if ran.returncode != 0:
        return Result(name, "run", False, states, ran.stderr.strip()[-200:])
    return Result(name, "run", True, states)


def _which_lfc() -> bool:
    import shutil

    return shutil.which("lfc") is not None


def _report(result: Result, show_states: int) -> None:
    if not result.ok:
        print(f"    {result.stage} FAILED: {result.detail}")
        return
    shown = result.states[:show_states]
    suffix = " ..." if len(result.states) > show_states else ""
    print(f"    {len(result.states)} entries: {' -> '.join(shown)}{suffix}")


def main() -> int:
    """Run the pipeline over the selected models; return an exit code."""
    parser = argparse.ArgumentParser(
        description=(
            "Build (CLI), lfc-compile, and run every Style-A showcase model."
        )
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="MODEL",
        help="run a subset (directory names, e.g. thermostat microwave)",
    )
    parser.add_argument(
        "--timeout",
        default="60 sec",
        help="logical stop time per run (fast mode; default: %(default)s)",
    )
    parser.add_argument(
        "--show-states",
        type=int,
        default=8,
        metavar="N",
        help="entered-state tokens to display per run (default: %(default)s)",
    )
    args = parser.parse_args()

    if not _which_lfc():
        sys.exit("lfc is not on PATH; install Lingua Franca first")

    build_root = SHOWCASE_DIR / "build"
    results: list[Result] = []
    models = _model_dirs(args.only)
    for index, model_dir in enumerate(models, start=1):
        print(f"[{index}/{len(models)}] {model_dir.name} ...", flush=True)
        result = _run_model(model_dir, build_root, args.timeout)
        results.append(result)
        _report(result, args.show_states)

    print("\n=== summary ===")
    width = max(len(r.model) for r in results)
    for r in results:
        status = "ok" if r.ok else f"FAILED ({r.stage})"
        print(f"  {r.model:<{width}}  {status}")
    failed = [r for r in results if not r.ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
