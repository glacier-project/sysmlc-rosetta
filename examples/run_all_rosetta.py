#!/usr/bin/env python3
"""Build, compile, and run every showcase model end to end.

For each model directory under ``models/showcase/``:

1. **Build**: ``sysmlc rosetta build`` translates the SysML state machine
   into a Lingua Franca project (``build/<model>/src/<Name>.lf``). When the
   model ships a ``values.yaml``, it is applied (disable with
   ``--no-values``).
2. **Compile**: an observer app (``<Name>App.lf``) is generated next to the
   machine — it imports the machine reactor, prints every ``current_state``
   announcement, and runs with ``fast: true`` plus a logical timeout, so
   runs finish instantly regardless of model timing — and ``lfc`` compiles
   it. (The observer also gives the project a distinct main-reactor name;
   compiling ``<Name>.lf`` directly would clash with its own trivial main.)
   When the build produces a **rig bench** reactor (outputs named
   ``<usage>_current_state``), the observer subscribes to each stream and
   labels every announcement ``STATE <usage>: <value>`` instead of the
   bare ``STATE: <value>`` used by single-machine builds.
3. **Run**: the binary executes and the observed state sequence is
   reported.

Each observer also carries the model's demo drivers (the same scripted
stimuli the run tests use), so every machine animates through a
representative scenario; pass ``--no-drivers`` for bare, unstimulated
runs.

Usage:
    python models/showcase/run_all.py [--only thermostat microwave]
        [--timeout "60 sec"] [--no-values] [--no-drivers]
        [--show-states N]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SHOWCASE_DIR = Path(__file__).resolve().parent

APP_TEMPLATE = """target Python {{
  fast: true,
  timeout: {timeout}
}}

import {reactor} from "{reactor}.lf"

main reactor {{
  m = new {reactor}()
{drivers}
  reaction(m.current_state) {{=
    print(f"STATE: {{m.current_state.value}}")
  =}}
}}
"""

RIG_APP_TEMPLATE = """target Python {{
  fast: true,
  timeout: {timeout}
}}

import {reactor} from "{reactor}.lf"

main reactor {{
  m = new {reactor}()
{observers}
}}
"""

# Per-stream reaction snippet joined with "\n"; indentation is load-bearing.
RIG_OBSERVER = """  reaction(m.{port}) {{=
    print(f"STATE {label}: {{m.{port}.value}}")
  =}}"""

# Demo stimuli per model (mirroring tests/backends/rosetta/test_run.py).
# Payloads are duck-typed SimpleNamespace stand-ins; sys is the
# module-level import the LF Python target always provides.
DRIVERS: dict[str, str] = {
    "traffic-light": """\
  timer ped(5 sec)
  reaction(ped) -> m.PedestrianRequest {=
    m.PedestrianRequest.set(True)
  =}""",
    "stopwatch": """\
  timer go(100 msec)
  reaction(go) -> m.StartCmd {=
    m.StartCmd.set(True)
  =}
  timer halt(3500 msec)
  reaction(halt) -> m.StopCmd {=
    m.StopCmd.set(True)
  =}""",
    "vending-machine": """\
  timer c1(100 msec)
  reaction(c1) -> m.Coin {=
    m.Coin.set(sys.modules["types"].SimpleNamespace(value=2))
  =}
  timer c2(200 msec)
  reaction(c2) -> m.Coin {=
    m.Coin.set(sys.modules["types"].SimpleNamespace(value=2))
  =}
  timer s1(300 msec)
  reaction(s1) -> m.Selection {=
    m.Selection.set(sys.modules["types"].SimpleNamespace(product="cola"))
  =}""",
    "furuta-pendulum": """\
  state thetas = {= [1.5, 0.9, 0.4, 0.3, 0.2, 0.1, 1.4] =}
  state i = 0
  timer sample(100 msec, 100 msec)
  reaction(sample) -> m.AngleReading {=
    if self.i < len(self.thetas):
      m.AngleReading.set(
        sys.modules["types"].SimpleNamespace(theta=self.thetas[self.i])
      )
      self.i += 1
  =}""",
    "microwave": """\
  timer go(100 msec)
  reaction(go) -> m.StartCmd {=
    m.StartCmd.set(True)
  =}""",
    "milling-workcell": """\
  timer power(100 msec)
  reaction(power) -> m.PowerOn {=
    m.PowerOn.set(True)
  =}
  timer start(1200 msec)
  reaction(start) -> m.StartBatch {=
    m.StartBatch.set(True)
  =}
  timer off(12 sec)
  reaction(off) -> m.Shutdown {=
    m.Shutdown.set(True)
  =}""",
    "batch-reactor": """\
  timer go(200 msec)
  reaction(go) -> m.StartRecipe {=
    m.StartRecipe.set(True)
  =}""",
    "charging-station": """\
  timer plug(200 msec)
  reaction(plug) -> m.PlugIn {=
    m.PlugIn.set(True)
  =}
  timer deny(1200 msec)
  reaction(deny) -> m.AuthResult {=
    m.AuthResult.set(sys.modules["types"].SimpleNamespace(code=7))
  =}
  timer grant(1600 msec)
  reaction(grant) -> m.AuthResult {=
    m.AuthResult.set(sys.modules["types"].SimpleNamespace(code=1))
  =}""",
    "level-crossing": """\
  timer approach(300 msec)
  reaction(approach) -> m.TrainApproaching {=
    m.TrainApproaching.set(True)
  =}
  timer passed(4 sec)
  reaction(passed) -> m.TrainPassed {=
    m.TrainPassed.set(True)
  =}""",
}


def _parse_state_line(line: str) -> str:
    """Normalize a STATE stdout line to its reported value.

    Single-machine observers print ``STATE: <value>``; rig observers
    print ``STATE <usage>: <value>``. The former yields ``<value>``,
    the latter ``<usage>: <value>``.
    """
    if line.startswith("STATE: "):
        return line.removeprefix("STATE: ")
    return line.removeprefix("STATE ")


@dataclass
class Result:
    """Outcome of one model's build/compile/run pipeline."""

    model: str
    stage: str  # the furthest stage reached: build | compile | run
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


def _build(model_dir: Path, src: Path, use_values: bool) -> tuple[bool, str]:
    command = [
        sys.executable,
        "-m",
        "sysmlc.cli",
        "rosetta",
        "build",
        str(model_dir),
        "-o",
        str(src),
    ]
    values = model_dir / "values.yaml"
    if use_values and values.exists():
        command += ["--values", str(values)]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        return False, completed.stderr.strip().splitlines()[-1]
    return True, "with values.yaml" if "--values" in command else ""


def _run_model(
    model_dir: Path,
    build_root: Path,
    timeout: str,
    use_values: bool,
    use_drivers: bool,
) -> Result:
    name = model_dir.name
    src = build_root / name / "src"
    src.mkdir(parents=True, exist_ok=True)

    ok, detail = _build(model_dir, src, use_values)
    if not ok:
        return Result(name, "build", False, [], detail)
    built_detail = detail

    (machine,) = [f for f in src.glob("*.lf") if not f.stem.endswith("App")]
    reactor = machine.stem
    app = src / f"{reactor}App.lf"
    streams = re.findall(
        r"^\s*output (\w+)_current_state$", machine.read_text(), re.M
    )
    if streams:  # rig models drive themselves; DRIVERS are not injected
        observers = "\n".join(
            RIG_OBSERVER.format(port=f"{label}_current_state", label=label)
            for label in streams
        )
        app.write_text(
            RIG_APP_TEMPLATE.format(
                reactor=reactor, timeout=timeout, observers=observers
            )
        )
    else:
        drivers = DRIVERS.get(name, "") if use_drivers else ""
        app.write_text(
            APP_TEMPLATE.format(
                reactor=reactor, timeout=timeout, drivers=drivers
            )
        )
    compiled = subprocess.run(
        ["lfc", str(app)], capture_output=True, text=True, timeout=600
    )
    if compiled.returncode != 0:
        return Result(
            name, "compile", False, [], compiled.stderr.strip()[-200:]
        )

    binary = build_root / name / "bin" / f"{reactor}App"
    ran = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=300
    )
    states = [
        _parse_state_line(line)
        for line in ran.stdout.splitlines()
        if line.startswith("STATE")
    ]
    if ran.returncode != 0:
        return Result(name, "run", False, states, ran.stderr.strip()[-200:])
    return Result(name, "run", True, states, built_detail)


def main() -> int:
    """Run the pipeline over the selected models; return an exit code."""
    parser = argparse.ArgumentParser(
        description="Build, lfc-compile, and run every showcase model."
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
        help="LOGICAL stop time per run (fast mode; default: %(default)s)",
    )
    parser.add_argument(
        "--no-values",
        action="store_true",
        help="ignore the models' shipped values.yaml files",
    )
    parser.add_argument(
        "--no-drivers",
        action="store_true",
        help="run without demo stimuli (machines idle in their initial "
        "state unless self-driving)",
    )
    parser.add_argument(
        "--show-states",
        type=int,
        default=8,
        metavar="N",
        help="announcements to display per run (default: %(default)s)",
    )
    args = parser.parse_args()

    if not _which_lfc():
        sys.exit("lfc is not on PATH; install Lingua Franca first")

    build_root = SHOWCASE_DIR / "build"
    results: list[Result] = []
    models = _model_dirs(args.only)
    for index, model_dir in enumerate(models, start=1):
        print(f"[{index}/{len(models)}] {model_dir.name} ...", flush=True)
        result = _run_model(
            model_dir,
            build_root,
            args.timeout,
            not args.no_values,
            not args.no_drivers,
        )
        results.append(result)
        _report(result, args.show_states)

    print("\n=== summary ===")
    width = max(len(r.model) for r in results)
    for r in results:
        status = "ok" if r.ok else f"FAILED ({r.stage})"
        print(f"  {r.model:<{width}}  {status}")
    failed = [r for r in results if not r.ok]
    return 1 if failed else 0


def _which_lfc() -> bool:
    import shutil

    return shutil.which("lfc") is not None


def _report(result: Result, show_states: int) -> None:
    if not result.ok:
        print(f"    {result.stage} FAILED: {result.detail}")
        return
    shown = result.states[:show_states]
    suffix = " ..." if len(result.states) > show_states else ""
    extra = f" ({result.detail})" if result.detail else ""
    print(
        f"    {len(result.states)} announcements{extra}: "
        f"{' -> '.join(shown)}{suffix}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
