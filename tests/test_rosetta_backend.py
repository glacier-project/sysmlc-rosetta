from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from sysmlc.backends import OutputOptions, discover_backends
from sysmlc.backends.rosetta.backend import RosettaBackend
from sysmlc.backends.rosetta.program import LfProgram
from sysmlc.errors import SerializationError
from sysmlc.sysml.loading import load_model
from tests.backends.test_sm_examples import SM_EXAMPLES_DIR

if TYPE_CHECKING:
    from pathlib import Path

    import syside

SM01_DIR = SM_EXAMPLES_DIR / "sm01-helloworld"
MACHINE_QN = "SM01::Machine"


@pytest.fixture(scope="module")
def backend() -> RosettaBackend:
    return RosettaBackend()


@pytest.fixture(scope="module")
def model() -> syside.Model:
    return load_model(SM01_DIR)


@pytest.fixture(scope="module")
def artifact(backend: RosettaBackend, model: syside.Model) -> LfProgram:
    result = backend.build(model, MACHINE_QN)
    assert isinstance(result, LfProgram)
    return result


def test_rosetta_backend_is_discoverable() -> None:
    backends = discover_backends()
    assert "rosetta" in backends
    assert isinstance(backends["rosetta"], RosettaBackend)


def test_formats(backend: RosettaBackend) -> None:
    assert backend.formats() == ["lf"]
    assert backend.default_format() == "lf"


def test_build_returns_lf_program(artifact: LfProgram) -> None:
    assert artifact.reactor.name == "Machine"


def test_serialize_lf(backend: RosettaBackend, artifact: LfProgram) -> None:
    text = backend.serialize(artifact, "lf")
    assert text.startswith("target Python\n")
    assert "reactor Machine {" in text


def test_serialize_rejects_unknown_format(
    backend: RosettaBackend, artifact: LfProgram
) -> None:
    with pytest.raises(SerializationError):
        backend.serialize(artifact, "json")


def test_write_emits_one_lf_file(
    backend: RosettaBackend, artifact: LfProgram, tmp_path: Path
) -> None:
    written = backend.write(artifact, OutputOptions(output_dir=tmp_path))
    assert written == [tmp_path / "Machine.lf"]
    assert written[0].read_text().startswith("target Python\n")


def test_write_creates_missing_output_dir(
    backend: RosettaBackend, artifact: LfProgram, tmp_path: Path
) -> None:
    nested = tmp_path / "nested" / "dir"
    backend.write(artifact, OutputOptions(output_dir=nested))
    assert (nested / "Machine.lf").is_file()


def test_write_respects_basename_override(
    backend: RosettaBackend, artifact: LfProgram, tmp_path: Path
) -> None:
    written = backend.write(
        artifact, OutputOptions(output_dir=tmp_path, basename="custom")
    )
    assert [p.name for p in written] == ["custom.lf"]


def test_write_rejects_unknown_format(
    backend: RosettaBackend, artifact: LfProgram, tmp_path: Path
) -> None:
    with pytest.raises(SerializationError):
        backend.write(
            artifact, OutputOptions(output_dir=tmp_path, formats=("yaml",))
        )


def test_summary_counts_modes_and_reactions(
    backend: RosettaBackend, artifact: LfProgram
) -> None:
    assert backend.summary(artifact) == (
        "LF program 'Machine': 2 modes, 2 reactions"
    )
