"""Unit tests for the furuta_physics pure-Python module.

Imports the module by file path since it lives under models/ (not the
sysmlc package).  No ``lf`` mark — these are fast, deterministic tests.
"""

import importlib.util
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Module import by file path
# ---------------------------------------------------------------------------

_MODULE_PATH = (
    Path(__file__).parents[3]
    / "models"
    / "showcase"
    / "furuta-pendulum"
    / "furuta_physics.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "furuta_physics", _MODULE_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["furuta_physics"] = mod
    spec.loader.exec_module(mod)
    return mod


_fp = _load_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(theta=0.0, d_theta=0.0, phi=0.0, d_phi=0.0) -> SimpleNamespace:
    return SimpleNamespace(theta=theta, d_theta=d_theta, phi=phi, d_phi=d_phi)


# ---------------------------------------------------------------------------
# Test 1: Determinism / purity
# ---------------------------------------------------------------------------


def test_step_is_deterministic_and_pure():
    """step() called twice on identical inputs gives identical outputs.

    Also verifies the input object is NOT mutated.
    """
    s = _state(theta=0.1, d_theta=0.02, phi=0.5, d_phi=-0.01)
    original_theta = s.theta
    original_d_theta = s.d_theta
    original_phi = s.phi
    original_d_phi = s.d_phi

    r1 = _fp.step(s, 0.3, 0.005)
    r2 = _fp.step(s, 0.3, 0.005)

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

    # Returns a new object
    assert r1 is not s


# ---------------------------------------------------------------------------
# Test 2: Stabilizer holds the inverted equilibrium
# ---------------------------------------------------------------------------


def test_stabilizer_holds_inverted_equilibrium():
    """stabilize_torque closes the loop and actually balances the pendulum.

    Start near the upright position (theta ≈ 0.05 rad) with no velocity.
    Integrate ~2000 steps under stabilize_torque.
    Assert:
      - |theta| never exceeds 0.3 rad throughout the run (no divergence)
      - |theta| is close to 0 at the end (active balance)

    If this test fails the ported gains / dynamics equations are wrong —
    fix the port, not the thresholds.
    """
    x = _state(theta=0.05, d_theta=0.0, phi=0.0, d_phi=0.0)
    phi0 = x.phi  # freeze arm reference as Stabilize would on entry
    n_steps = 2000
    max_theta_seen = 0.0

    for _ in range(n_steps):
        u = _fp.stabilize_torque(x, phi0=phi0)
        x = _fp.step(x, u, _fp.H)
        max_theta_seen = max(max_theta_seen, math.fabs(x.theta))

    # Must never diverge
    assert max_theta_seen < 0.3, (
        f"Pendulum diverged: max |theta| = {max_theta_seen:.4f} rad "
        "(expected < 0.3). Check ported gains/dynamics."
    )

    # Must converge close to upright
    final_theta = math.fabs(_fp.restrict_angle(x.theta))
    assert final_theta < 0.05, (
        f"Pendulum did not converge: final |theta| = {final_theta:.4f} rad "
        "(expected < 0.05). Check ported gains/dynamics."
    )


# ---------------------------------------------------------------------------
# Test 3: Swing-up adds energy toward upright
# ---------------------------------------------------------------------------


def test_swingup_adds_energy():
    """swingup_torque pumps energy toward upright from hanging-down rest.

    Start hanging straight down (theta = pi, d_theta = 0).
    Integrate ~500 steps.
    Assert:
      - The pendulum has moved (|restrict_angle(theta)| has decreased from pi)
      - d_theta is non-zero (the pendulum is moving)

    This is a loose check -- we just verify energy injection is happening,
    not that the full swing-up completes.
    """
    x = _state(theta=math.pi, d_theta=0.0, phi=0.0, d_phi=0.0)
    n_steps = 500

    for _ in range(n_steps):
        u = _fp.swingup_torque(x)
        x = _fp.step(x, u, _fp.H)

    # Should have moved significantly away from hanging-down rest
    final_angle = math.fabs(_fp.restrict_angle(x.theta))
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
def test_restrict_angle(theta, expected):
    result = _fp.restrict_angle(theta)
    assert math.isclose(result, expected, abs_tol=1e-9), (
        f"restrict_angle({theta:.4f}) = {result:.6f}, expected {expected:.6f}"
    )
