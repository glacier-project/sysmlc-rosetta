from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import pytest
from sysmlc.backends import OutputOptions, discover_backends
from sysmlc.errors import SerializationError
from sysmlc.sysml.loading import load_model
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.backend import RosettaBackend
from sysmlc_rosetta.program import LfProgram

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


def test_supported_foreign_artifact_languages(backend: RosettaBackend) -> None:
    assert backend.supported_foreign_artifact_languages() == frozenset(
        {"python"}
    )


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


SM05_DIR = SM_EXAMPLES_DIR / "sm05-chained-references"
CHAIN_GUARD_QN = "SM05::MachineChainGuard"  # has a composite `Point` type


@pytest.fixture(scope="module")
def typed_artifact(backend: RosettaBackend) -> LfProgram:
    result = backend.build(load_model(SM05_DIR), CHAIN_GUARD_QN)
    assert isinstance(result, LfProgram)
    return result


def test_generate_output_is_one_lf_file_without_types(
    backend: RosettaBackend, artifact: LfProgram, tmp_path: Path
) -> None:
    output = backend.generate_output(
        artifact, OutputOptions(output_dir=tmp_path)
    )
    assert [str(p) for p in output.paths] == ["Machine.lf"]
    assert output.content(PurePosixPath("Machine.lf")).startswith(
        "target Python\n"
    )
    assert list(tmp_path.iterdir()) == []  # nothing written


def test_generate_output_adds_the_types_module(
    backend: RosettaBackend, typed_artifact: LfProgram, tmp_path: Path
) -> None:
    output = backend.generate_output(
        typed_artifact, OutputOptions(output_dir=tmp_path, basename="custom")
    )
    assert [str(p) for p in output.paths] == [
        "custom.lf",
        "MachineChainGuard_types.py",
    ]
    types_module = output.content(PurePosixPath("MachineChainGuard_types.py"))
    assert "class Point:" in types_module
    assert types_module.endswith("\n")


def test_generate_output_rejects_unknown_format(
    backend: RosettaBackend, artifact: LfProgram, tmp_path: Path
) -> None:
    with pytest.raises(SerializationError):
        backend.generate_output(
            artifact, OutputOptions(output_dir=tmp_path, formats=("yaml",))
        )


def test_write_emits_the_types_module_beside_the_program(
    backend: RosettaBackend, typed_artifact: LfProgram, tmp_path: Path
) -> None:
    written = backend.write(typed_artifact, OutputOptions(output_dir=tmp_path))
    assert written == [
        tmp_path / "MachineChainGuard.lf",
        tmp_path / "MachineChainGuard_types.py",
    ]
    assert "class Point:" in written[1].read_text()
