"""Standing 1B inertness guard: no showcase run collides two triggers.

Builds each Style-A showcase part-system, injects a FIRE log into every
non-entry reaction (reactor class, mode, trigger signature, instance id,
lf.tag()), runs it, and asserts no (instance, mode, tag) has >=2 distinct
signal/after transition reactions fire.  A trigger is a 1B transition
trigger iff its signature has no '.' (dotted = child-port plumbing) and is
not the entry (reset, startup).  This pins the verified 0-collision result.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from tests.backends.test_showcase import SHOWCASE_DIR

pytestmark = pytest.mark.lf

REACTOR_RE = re.compile(r"^reactor (\w+) \{")
MODE_RE = re.compile(r"^\s*(?:initial )?mode (\w+) \{")
REACT_RE = re.compile(r"^(\s*)reaction\((.*?)\)")


def _inject(lf_text: str) -> str:
    out: list[str] = []
    reactor, mode = "main", "(rl)"
    for line in lf_text.splitlines():
        rm = REACTOR_RE.match(line)
        if rm:
            reactor, mode = rm.group(1), "(rl)"
        elif line.strip() == "main reactor {":
            reactor, mode = "main", "(rl)"
        mm = MODE_RE.match(line)
        if mm:
            mode = mm.group(1)
        out.append(line)
        xr = REACT_RE.match(line)
        if xr and line.rstrip().endswith("{="):
            trig = xr.group(2).strip()
            if "reset" in trig and "startup" in trig:
                continue
            pad = xr.group(1) + "  "
            head = f"FIRE|{reactor}|{mode}|{trig}|"
            out.append(
                f'{pad}print("{head}" + str(id(self)) + " @ " '
                f"+ str(lf.tag()), flush=True)"
            )
    return "\n".join(out) + "\n"


def _is_transition_trig(trig: str) -> bool:
    return "." not in trig and trig not in (
        "reset, startup",
        "startup",
        "reset",
        "current_state",
    )


SHOWCASE_DIRS = sorted(
    d for d in SHOWCASE_DIR.iterdir() if d.is_dir() and any(d.glob("*.sysml"))
)


@pytest.mark.parametrize("model_dir", SHOWCASE_DIRS, ids=lambda d: d.name)
def test_showcase_has_no_1b_collision(model_dir: Path, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    cmd = [
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
        "30 sec",
    ]
    pys = list(model_dir.glob("*.py"))
    if len(pys) == 1:
        cmd += ["--python", str(pys[0])]
    build = subprocess.run(cmd, capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    lfs = list(src.glob("*.lf"))
    assert len(lfs) == 1
    main = src / "Main.lf"
    main.write_text(_inject(lfs[0].read_text()))
    lfs[0].unlink()
    (src / "sitecustomize.py").write_text("")
    compiled = subprocess.run(
        ["lfc", str(main)], capture_output=True, text=True, timeout=600
    )
    assert compiled.returncode == 0, compiled.stderr
    env = {**os.environ, "PYTHONPATH": str(src)}
    run = subprocess.run(
        [str(tmp_path / "bin" / "Main")],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert run.returncode == 0, run.stderr
    fires: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for ln in run.stdout.splitlines():
        if not ln.startswith("FIRE|"):
            continue
        head, _, tag = ln.partition(" @ ")
        _, reactor, mode, trig, inst = head.split("|", 4)
        fires[(inst, reactor, mode, tag)].append(trig)
    assert fires, "no FIRE lines captured — injection or run failed"
    collisions = {
        key: sorted({t for t in trigs if _is_transition_trig(t)})
        for key, trigs in fires.items()
        if len({t for t in trigs if _is_transition_trig(t)}) >= 2
    }
    assert not collisions, f"1B collisions in {model_dir.name}: {collisions}"
