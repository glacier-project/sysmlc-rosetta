from pathlib import Path

import pytest
from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.sysml.loading import load_model

from tests.test_recording import RecordingBuilder

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# The pinned core still registers its own bundled rosetta entry point, so
# backend discovery finds two providers for the name and rejects the
# duplicate, which makes the CLI unusable from this repository. Every test
# that reaches discovery carries this marker. The expectation is strict:
# once the core stops shipping the backend these tests pass, the strict
# marker turns each pass into a failure, and the marker is removed in the
# same commit that bumps the lock.
bundled_backend_conflict = pytest.mark.xfail(
    strict=True,
    reason="the pinned core still registers its own rosetta entry point",
)


def record(model_dir: Path, qn: str) -> RecordingBuilder:
    """Drive ``qn`` from ``model_dir`` into a RecordingBuilder."""
    builder = RecordingBuilder()
    StateMachineDriver(load_model(model_dir)).run(qn, builder)
    return builder
