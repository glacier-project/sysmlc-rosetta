from pathlib import Path

import syside

from sysmlc.backends.rosetta.codegen import PreambleNeeds, py_type
from sysmlc.sysml.loading import load_model
from sysmlc.sysml.queries import resolve


def test_no_types_means_no_module_and_no_import():
    needs = PreambleNeeds()
    needs.types_module = "Foo_types"
    assert needs.companion_module_lines() == []
    assert needs.preamble_lines() == []
    assert needs.has_types is False


def test_dataclass_goes_to_module_preamble_imports_it():
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


def test_preamble_orders_imports_math_then_external_then_types():
    needs = PreambleNeeds()
    needs.types_module = "Foo_types"
    needs.uses_math = True
    needs.register_external(module="phys", names=frozenset({"step"}))
    needs.used_external.add("step")
    needs.register_dataclass("Pt", ("@dataclass", "class Pt:", "    pass"))
    assert needs.preamble_lines() == [
        "import math",
        "from phys import step",
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
