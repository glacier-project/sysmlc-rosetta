"""Lingua Franca target languages for rosetta's generated programs."""

from sysmlc_rosetta.targets.base import LfTarget
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.python import PythonTarget

#: Every target language, by the name ``--target`` selects it with.
TARGETS: dict[str, type[LfTarget]] = {
    target.name: target for target in (PythonTarget, CTarget)
}

__all__ = ["TARGETS", "CTarget", "LfTarget", "PythonTarget"]
