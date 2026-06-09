from __future__ import annotations

from sysmlc.backends import discover_backends
from sysmlc.backends.rosetta.backend import RosettaBackend


def test_rosetta_backend_is_discoverable() -> None:
    backends = discover_backends()
    assert "rosetta" in backends
    assert isinstance(backends["rosetta"], RosettaBackend)
