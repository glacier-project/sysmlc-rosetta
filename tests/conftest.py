from pathlib import Path

from sysmlc.semantics.statemachine.driver import StateMachineDriver
from sysmlc.sysml.loading import load_model
from tests.test_recording import RecordingBuilder

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def record(model_dir: Path, qn: str) -> RecordingBuilder:
    """Drive ``qn`` from ``model_dir`` into a RecordingBuilder."""
    builder = RecordingBuilder()
    StateMachineDriver(load_model(model_dir)).run(qn, builder)
    return builder
