from __future__ import annotations

import importlib
import importlib.util
import math
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

import syside

from sysmlc.sysml.textual_representation import (
    extract_textual,
    write_module,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SYSMLC = str(_PROJECT_ROOT / ".venv" / "bin" / "sysmlc")
_MODEL_DIR = str(
    _PROJECT_ROOT / "models" / "showcase" / "furuta-pendulum_inline"
)
_SYSML_FILE = str(Path(_MODEL_DIR) / "furuta_pendulum_inline.sysml")


# ---------------------------------------------------------------------------
# Session fixture: generate furutaSystem_types.py once per test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def physics_module(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    """Import the physics module extracted from the inline furuta reps."""
    out = tmp_path_factory.mktemp("gen_code")

    model, _ = syside.load_model([_SYSML_FILE])

    result = extract_textual(
        model, "furuta::physics", module_name="furuta_physics_inline"
    )
    assert result is not None
    stem, src_lines = result

    write_module(src_lines, out, stem)

    if str(out) not in sys.path:
        sys.path.insert(0, str(out))

    sys.modules.pop(stem, None)

    mod = importlib.import_module("furuta_physics_inline")
    return mod


@pytest.fixture(scope="session")
def generated_types(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    """Generate furutaSystem_types.py by building the furuta part.

    Puts the output dir on sys.path so that the runtime
    ``from furutaSystem_types import PendulumState`` inside ``step()``
    resolves.  Also forces a reload of ``furuta_physics`` under its canonical
    module name so any previously-cached module sees the new path.

    Returns a namespace with ``PendulumState`` and ``AngleReading`` classes.
    """
    out = tmp_path_factory.mktemp("furuta_gen")
    subprocess.run(
        [
            _SYSMLC,
            "rosetta",
            "build",
            _MODEL_DIR,
            "-o",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    # Put the generated dir first so furutaSystem_types is importable.
    if str(out) not in sys.path:
        sys.path.insert(0, str(out))
    # Clear any stale cached module so the fresh one is imported.
    sys.modules.pop("furutaSystem_types", None)
    types_mod = importlib.import_module("furutaSystem_types")

    return types_mod


# ---------------------------------------------------------------------------
# Test 1: Determinism / purity
# ---------------------------------------------------------------------------


def test_step_is_deterministic_and_pure(
    generated_types: ModuleType, physics_module: ModuleType
) -> None:
    """step() called twice on identical inputs gives identical outputs.

    Also verifies the input object is NOT mutated and the return type
    is ``PendulumState`` (the generated companion dataclass).
    """
    s = generated_types.PendulumState(
        theta=0.1, d_theta=0.02, phi=0.5, d_phi=-0.01
    )
    original_theta = s.theta
    original_d_theta = s.d_theta
    original_phi = s.phi
    original_d_phi = s.d_phi

    r1 = physics_module.step(s, 0.3, 0.005)
    r2 = physics_module.step(s, 0.3, 0.005)

    # Outputs are identical
    assert r1.theta == r2.theta
    assert r1.d_theta == r2.d_theta
    assert r1.phi == r2.phi
    assert r1.d_phi == r2.d_phi

    # Input was not mutated
    assert s.theta == original_theta
    assert s.d_theta == original_d_theta
    assert s.phi == original_phi
    assert s.d_phi == original_d_phi

    # Returns a new object of the generated type
    assert r1 is not s
    assert type(r1).__name__ == "PendulumState"


# ---------------------------------------------------------------------------
# Test 2: Stabilizer holds the inverted equilibrium
# ---------------------------------------------------------------------------


def test_stabilizer_holds_inverted_equilibrium(
    generated_types: ModuleType, physics_module: ModuleType
) -> None:
    """stabilize_torque closes the loop and actually balances the pendulum.

    Start near the upright position (theta ≈ 0.05 rad) with no velocity.
    Integrate ~2000 steps under stabilize_torque.
    Assert:
      - |theta| never exceeds 0.3 rad throughout the run (no divergence)
      - |theta| is close to 0 at the end (active balance)

    If this test fails the ported gains / dynamics equations are wrong —
    fix the port, not the thresholds.
    """
    x = generated_types.PendulumState(
        theta=0.05, d_theta=0.0, phi=0.0, d_phi=0.0
    )
    n_steps = 2000
    max_theta_seen = 0.0

    for _ in range(n_steps):
        r = generated_types.AngleReading(
            theta=x.theta, d_theta=x.d_theta, phi=x.phi, d_phi=x.d_phi
        )
        u = physics_module.stabilize_torque(r)
        x = physics_module.step(x, u, 0.005)
        max_theta_seen = max(max_theta_seen, math.fabs(x.theta))

    # Must never diverge
    assert max_theta_seen < 0.3, (
        f"Pendulum diverged: max |theta| = {max_theta_seen:.4f} rad "
        "(expected < 0.3). Check ported gains/dynamics."
    )

    # Must converge close to upright
    final_theta = math.fabs(physics_module.restrict_angle(x.theta))
    assert final_theta < 0.05, (
        f"Pendulum did not converge: final |theta| = {final_theta:.4f} rad "
        "(expected < 0.05). Check ported gains/dynamics."
    )


# ---------------------------------------------------------------------------
# Test 3: Swing-up adds energy toward upright
# ---------------------------------------------------------------------------


def test_swingup_adds_energy(
    generated_types: ModuleType, physics_module: ModuleType
) -> None:
    """swingup_torque pumps energy toward upright from hanging-down rest.

    Start hanging straight down (theta = pi, d_theta = 0).
    Integrate ~500 steps.
    Assert:
      - The pendulum has moved (|restrict_angle(theta)| has decreased from pi)
      - d_theta is non-zero (the pendulum is moving)

    This is a loose check -- we just verify energy injection is happening,
    not that the full swing-up completes.
    """
    x = generated_types.PendulumState(
        theta=math.pi, d_theta=0.0, phi=0.0, d_phi=0.0
    )
    n_steps = 500

    for _ in range(n_steps):
        r = generated_types.AngleReading(
            theta=x.theta, d_theta=x.d_theta, phi=x.phi, d_phi=x.d_phi
        )
        u = physics_module.swingup_torque(r)
        x = physics_module.step(x, u, 0.005)

    # Should have moved significantly away from hanging-down rest
    final_angle = math.fabs(physics_module.restrict_angle(x.theta))
    assert final_angle < math.pi - 0.05, (
        f"Swing-up made no progress: |theta| = {final_angle:.4f} rad "
        "(expected < {math.pi - 0.05:.4f}). Energy not being added."
    )

    # Pendulum must be moving
    assert math.fabs(x.d_theta) > 0.01, (
        f"Pendulum still nearly stationary after swing-up: "
        f"d_theta = {x.d_theta:.6f} rad/s"
    )


# ---------------------------------------------------------------------------
# Test 4: restrict_angle stays in (-pi, pi]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "theta,expected",
    [
        (0.0, 0.0),
        # +pi: fmod(pi+pi, 2pi)=0 exactly -> (0-pi)*sign(pi)= -pi
        # -pi: fmod(pi+pi, 2pi)=0 exactly -> (0-pi)*sign(-pi)= +pi
        # Both are equivalent representations of the same angle.
        (math.pi, -math.pi),
        (-math.pi, math.pi),
        (2 * math.pi, 0.0),
        (3 * math.pi / 2, -math.pi / 2),
        (-3 * math.pi / 2, math.pi / 2),
    ],
)
def test_restrict_angle(
    physics_module: ModuleType, theta: float, expected: float
) -> None:
    result = physics_module.restrict_angle(theta)
    assert math.isclose(result, expected, abs_tol=1e-9), (
        f"restrict_angle({theta:.4f}) = {result:.6f}, expected {expected:.6f}"
    )
