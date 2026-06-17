from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

import syside as _syside

from sysmlc.backends.base import Backend, OutputOptions
from sysmlc.backends.rosetta.builder import build_program, finalize
from sysmlc.backends.rosetta.codegen import PreambleNeeds
from sysmlc.backends.rosetta.parts import build_part_program, compose_exhibits
from sysmlc.backends.rosetta.program import LfProgram
from sysmlc.backends.rosetta.serialize import to_lf
from sysmlc.errors import SerializationError, UnsupportedConstructError
from sysmlc.semantics.statemachine.interface import machine_interface
from sysmlc.sysml.queries import exhibited_state_defs, resolve

if TYPE_CHECKING:
    from pathlib import Path

    import syside

logger = logging.getLogger(__name__)


class RosettaBackend(Backend):
    """Lingua Franca backend for generating LF programs from SysML."""

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
    ) -> LfProgram:
        """Build the composed LF program for a testbench rig.

        Resolves the rig part def, unpacks its two exhibited state machines,
        checks for same-def-twice and bidirectional-same-name-signal errors,
        warns about unwired inputs, then delegates to
        :func:`~sysmlc.backends.rosetta.parts.compose_exhibits`.
        """
        rig = resolve(model, _syside.PartDefinition, rig_qn)
        (usage_a, def_a), (usage_b, def_b) = exhibited_state_defs(model, rig)
        qn_a = str(def_a.qualified_name)
        qn_b = str(def_b.qualified_name)
        if qn_a == qn_b:
            raise UnsupportedConstructError(
                f"rig {rig.name!r} exhibits {qn_a!r} twice; a rig composes "
                "two distinct state defs"
            )
        face_a = machine_interface(model, qn_a)
        face_b = machine_interface(model, qn_b)
        both = (face_a.sent & face_b.accepted) & (face_b.sent & face_a.accepted)
        if both:
            raise UnsupportedConstructError(
                f"signal(s) {sorted(both)!r} are sent by both machines; "
                "bidirectional same-name signals are not supported"
            )
        for usage, face, peer in (
            (usage_a, face_a, face_b),
            (usage_b, face_b, face_a),
        ):
            for sig in sorted(face.accepted - peer.sent - face.sent):
                logger.warning(
                    "machine %r accepts %r but its peer never sends it; the "
                    "input port stays unwired",
                    usage,
                    sig,
                )
        composite_name = rig_qn.split("::")[-1]
        needs = PreambleNeeds()
        needs.types_module = f"{composite_name}_types"
        if external is not None:
            needs.register_external(module=external[0], names=external[1])
        children, composite = compose_exhibits(
            model,
            composite_name,
            ((usage_a, qn_a), (usage_b, qn_b)),
            needs,
        )
        program = LfProgram(
            reactors=(*children, composite),
            preamble=tuple(needs.preamble_lines()),
        )
        return finalize(program, needs, external)

    def build_part(
        self,
        model: syside.Model,
        usage_qn: str,
        *,
        target_options: tuple[tuple[str, str], ...] = (),
        external: tuple[str, frozenset[str]] | None = None,
    ) -> object:
        """Build the LF program (main reactor) for a top-level part usage.

        Args:
            model: Loaded syside model.
            usage_qn: Qualified name of the top-level part usage.
            target_options: Key/value pairs for the LF target header.
            external: Optional ``(module_stem, function_names)`` pair for
                ``--python`` external calc-def backing.
        """
        return build_part_program(
            model,
            usage_qn,
            target_options=target_options,
            external=external,
        )

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
        written = [path]
        if artifact.types_module_lines:
            assert artifact.types_module_name is not None
            module_path = (
                options.output_dir / f"{artifact.types_module_name}.py"
            )
            module_path.write_text(
                "\n".join(artifact.types_module_lines) + "\n"
            )
            written.append(module_path)
        return written

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
