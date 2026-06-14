from __future__ import annotations

from typing import TYPE_CHECKING, override

from sysmlc.backends.base import Backend, OutputOptions
from sysmlc.backends.rosetta.builder import build_program
from sysmlc.backends.rosetta.composition import build_rig_program
from sysmlc.backends.rosetta.program import LfProgram
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.errors import SerializationError

if TYPE_CHECKING:
    from pathlib import Path

    import syside


class RosettaBackend(Backend):
    """Lingua Franca backend for SysML state definitions."""

    def __init__(self) -> None:
        super().__init__(
            name="rosetta",
            description=(
                "Generate Lingua Franca programs (Python target) from "
                "SysML state definitions."
            ),
            formats=(("lf", "Lingua Franca (Python target) program"),),
        )

    @override
    def build(
        self,
        model: syside.Model,
        element_qn: str,
        *,
        external: tuple[str, frozenset[str]] | None = None,
    ) -> object:
        """Build the Lingua Franca program for the given state definition."""
        return build_program(model, element_qn, external=external)

    def build_composition(
        self,
        model: syside.Model,
        rig_qn: str,
        *,
        external: tuple[str, frozenset[str]] | None = None,
    ) -> object:
        """Build the composed LF program for a testbench rig."""
        return build_rig_program(model, rig_qn, external=external)

    @override
    def serialize(self, artifact: object, fmt: str) -> str:
        """Serialize the Lingua Franca program to the requested format."""
        if not isinstance(artifact, LfProgram):
            raise SerializationError("expected an LfProgram artifact")
        if fmt == "lf":
            return to_lf(artifact)
        raise SerializationError(f"unsupported format: {fmt!r}")

    @override
    def write(self, artifact: object, options: OutputOptions) -> list[Path]:
        """Write the program as ``<basename>.lf`` in the output directory.

        The only supported format is ``"lf"``; passing any other value in
        ``options.formats`` raises :exc:`~sysmlc.errors.SerializationError`.
        The basename defaults to the reactor name when not supplied.
        """
        if not isinstance(artifact, LfProgram):
            raise SerializationError("expected an LfProgram artifact")
        formats = options.formats or tuple(self.formats())
        for fmt in formats:
            if fmt != "lf":
                raise SerializationError(f"unsupported format: {fmt!r}")
        basename = options.basename or artifact.reactor.name
        options.output_dir.mkdir(parents=True, exist_ok=True)
        path = options.output_dir / f"{basename}.lf"
        path.write_text(self.serialize(artifact, "lf"))
        return [path]

    @override
    def summary(self, artifact: object) -> str:
        """Return a one-line description of the built program."""
        if not isinstance(artifact, LfProgram):
            return self.name
        reactor = artifact.reactor
        modes = len(reactor.modes)
        reactions = len(reactor.reactions) + sum(
            len(mode.reactions) for mode in reactor.modes
        )
        return (
            f"LF program {reactor.name!r}: {modes} modes, {reactions} reactions"
        )
