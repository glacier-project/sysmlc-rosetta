from sysmlc.backends.rosetta.codegen import PreambleNeeds


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
