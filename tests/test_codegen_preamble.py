from pathlib import Path

import syside
from sysmlc.sysml.loading import load_model
from sysmlc.sysml.queries import resolve
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

from sysmlc_rosetta.codegen import PreambleNeeds, py_type


def test_no_types_means_no_module_and_no_import() -> None:
    needs = PreambleNeeds()
    needs.types_module = "Foo_types"
    assert needs.companion_module_lines() == []
    assert needs.preamble_lines() == []
    assert needs.has_types is False


def test_dataclass_goes_to_module_preamble_imports_it() -> None:
    needs = PreambleNeeds()
    needs.types_module = "Foo_types"
    needs.register_dataclass(
        "Pt", ("@dataclass", "class Pt:", "    x: float = 1.0")
    )
    assert needs.has_types is True
    assert needs.companion_module_lines() == [
        "from dataclasses import dataclass",
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
    needs.register_dataclass("Pt", ("@dataclass", "class Pt:", "    pass"))
    assert needs.preamble_lines() == [
        "import math",
        "from ramp import step",
        "from Foo_types import Pt",
    ]


def test_py_type_maps_scalars(tmp_path: Path) -> None:
    sysml = tmp_path / "D.sysml"
    sysml.write_text(
        "package P {\n"
        "  private import ScalarValues::*;\n"
        "  attribute def D {\n"
        "    attribute a : Real;\n"
        "    attribute b : Integer;\n"
        "    attribute c : Boolean;\n"
        "    attribute d : String;\n"
        "    attribute e;\n"
        "  }\n"
        "}\n"
    )
    model = load_model(tmp_path)
    d = resolve(model, syside.AttributeDefinition, "P::D")
    types = {a.name: py_type(a) for a in d.owned_attributes.collect()}
    assert types == {
        "a": "float",
        "b": "int",
        "c": "bool",
        "d": "str",
        "e": "object",
    }
