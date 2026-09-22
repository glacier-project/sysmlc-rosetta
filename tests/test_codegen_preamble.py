from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.codegen import PreambleNeeds


def test_no_types_means_no_module_and_no_import() -> None:
    needs = PreambleNeeds()
    needs.types_module = "Foo_types"
    assert needs.companion_module_lines() == []
    assert needs.preamble_lines() == []
    assert needs.has_types is False


def test_dataclass_goes_to_module_preamble_imports_it() -> None:
    needs = PreambleNeeds()
    needs.types_module = "Foo_types"
    needs.dataclasses.register(
        "Pt", "P::Pt", ("@dataclass", "class Pt:", "    x: float = 1.0")
    )
    assert needs.has_types is True
    assert needs.companion_module_lines() == [
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "",
        "@dataclass",
        "class Pt:",
        "    x: float = 1.0",
    ]
    assert needs.preamble_lines() == ["from Foo_types import Pt"]


def test_preamble_orders_imports_math_then_external_then_types() -> None:
    from sysmlc.sysml.foreign_artifact.base import ForeignArtifact

    needs = PreambleNeeds()
    needs.types_module = "Foo_types"
    needs.uses_math = True
    artifact = ForeignArtifact(
        SM_EXAMPLES_DIR / "sm15-external" / "ramp.py",
        "python",
    )
    needs.external = [artifact]
    needs.used_external[artifact] = {"step"}
    needs.dataclasses.register(
        "Pt", "P::Pt", ("@dataclass", "class Pt:", "    pass")
    )
    assert needs.preamble_lines() == [
        "import math",
        "from ramp import step",
        "from Foo_types import Pt",
    ]
