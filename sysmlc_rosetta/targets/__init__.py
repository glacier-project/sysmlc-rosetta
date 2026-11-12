"""Lingua Franca target languages for rosetta's generated programs."""

from sysmlc_rosetta.targets.base import LfTarget
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.python import PythonTarget
from sysmlc_rosetta.targets.uc import UcTarget

#: Every target language, by the name ``--target`` selects it with.
TARGETS: dict[str, type[LfTarget]] = {
    target.name: target for target in (PythonTarget, CTarget, UcTarget)
}

__all__ = ["TARGETS", "CTarget", "LfTarget", "PythonTarget", "UcTarget"]
