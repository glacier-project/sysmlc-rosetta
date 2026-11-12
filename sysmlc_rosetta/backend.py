from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, override

import syside as _syside
from sysmlc.backends import BuildOption, GeneratedFile, GeneratedOutput
from sysmlc.backends.base import Backend, OutputOptions
from sysmlc.errors import SerializationError, UnsupportedConstructError
from sysmlc.semantics.statemachine.interface import machine_interface
from sysmlc.semantics.statemachine.signals import SignalCatalog
from sysmlc.sysml.queries import exhibited_state_defs, resolve

from sysmlc_rosetta.builder import build_program, finalize
from sysmlc_rosetta.codegen import PreambleNeeds
from sysmlc_rosetta.parts import build_part_program, compose_exhibits
from sysmlc_rosetta.program import LfProgram
from sysmlc_rosetta.serialize import to_lf
from sysmlc_rosetta.targets import TARGETS

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import syside
    from sysmlc.sysml.foreign_artifact.base import ForeignArtifact

    from sysmlc_rosetta.targets.base import LfTarget

logger = logging.getLogger(__name__)


class RosettaBackend(Backend):
    """Lingua Franca backend for generating LF programs from SysML.

    An unconfigured backend (``target=None``) generates for Python and
    declares the foreign-artifact languages of every target, so the CLI
    offers all their flags. :meth:`configured` returns a backend that
    consumes only its own target's languages.

    Args:
        target: The LF target language of the generated programs, a key of
            :data:`~sysmlc_rosetta.targets.TARGETS`, or None.

    Raises:
        ValueError: If ``target`` is unknown.
    """

    def __init__(self, target: str | None = None) -> None:
        if target is not None and target not in TARGETS:
            raise ValueError(f"unknown LF target {target!r}")
        languages = (
            sorted(
                {lang for t in TARGETS.values() for lang in t.foreign_languages}
            )
            if target is None
            else TARGETS[target].foreign_languages
        )
        super().__init__(
            name="rosetta",
            description=(
                "Generate Lingua Franca programs (Python, C or uC target) from "
                "SysML state definitions."
            ),
            formats=(("lf", "Lingua Franca program"),),
            foreign_artifact_languages=languages,
        )
        self.target = target or "python"

    @override
    def build_options(self) -> tuple[BuildOption, ...]:
        """Rosetta's ``--target``: the LF target language."""
        return (
            BuildOption(
                "target",
                "LF target language of the generated program",
                choices=tuple(TARGETS),
                default="python",
            ),
        )

    @override
    def configured(self, options: Mapping[str, str | None]) -> RosettaBackend:
        """Return a backend generating for the selected ``--target``."""
        return RosettaBackend(options.get("target") or "python")

    def _new_target(self) -> LfTarget:
        """A fresh target object; one serves one built program."""
        return TARGETS[self.target]()

    def _own(
        self, external: list[ForeignArtifact] | None
    ) -> list[ForeignArtifact] | None:
        """The artifacts written in the target's languages."""
        if external is None:
            return None
        languages = TARGETS[self.target].foreign_languages
        return [a for a in external if a.lang in languages]

    @override
    def build(
        self,
        model: syside.Model,
        element_qn: str,
        external: list[ForeignArtifact] | None = None,
        strict_extern: bool = False,
    ) -> object:
        """Build the Lingua Franca program for the given state definition."""
        return build_program(
            model,
            element_qn,
            external=self._own(external),
            target=self._new_target(),
        )

    @override
    def consumes_python_support(self) -> bool:
        """Rosetta's Python target is backed by ``--python`` modules."""
        return self.target == "python"

    @override
    def owns_external_delivery(self) -> bool:
        """The C target ships its artifacts among its generated files."""
        return self.target != "python"

    @override
    def accepts_target_options(self) -> bool:
        """Rosetta emits an LF target header set by ``--timeout``/``--fast``."""
        return True

    @override
    def build_composition(
        self,
        model: syside.Model,
        element_qn: str,
        *,
        external: list[ForeignArtifact] | None = None,
        strict_extern: bool = False,
    ) -> LfProgram:
        """Build the composed LF program for a testbench rig.

        Resolves the rig part def, unpacks its two exhibited state machines,
        checks for same-def-twice and bidirectional-same-name-signal errors,
        warns about unwired inputs, then delegates to
        :func:`~sysmlc_rosetta.parts.compose_exhibits`.
        """
        rig = resolve(model, _syside.PartDefinition, element_qn)
        (usage_a, def_a), (usage_b, def_b) = exhibited_state_defs(model, rig)
        qn_a = str(def_a.qualified_name)
        qn_b = str(def_b.qualified_name)
        if qn_a == qn_b:
            raise UnsupportedConstructError(
                f"rig {rig.name!r} exhibits {qn_a!r} twice; a rig composes "
                "two distinct state defs"
            )
        catalog = SignalCatalog(model, [qn_a, qn_b])
        face_a = machine_interface(model, qn_a, catalog=catalog)
        face_b = machine_interface(model, qn_b, catalog=catalog)
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
        composite_name = element_qn.split("::")[-1]
        needs = PreambleNeeds(catalog, target=self._new_target())
        needs.types_module = f"{composite_name}_types"
        external = self._own(external)
        if external:
            needs.external = external
        children, composite = compose_exhibits(
            model,
            composite_name,
            ((usage_a, qn_a), (usage_b, qn_b)),
            needs,
        )
        program = LfProgram(
            reactors=(*children, composite),
            constraints=tuple(needs.constraints.values()),
        )
        return finalize(program, needs)

    @override
    def build_part(
        self,
        model: syside.Model,
        usage_qn: str,
        *,
        target_options: tuple[tuple[str, str], ...] = (),
        external: list[ForeignArtifact] | None = None,
        strict_extern: bool = False,
    ) -> object:
        """Build the LF program (main reactor) for a top-level part usage.

        Args:
            model: Loaded syside model.
            usage_qn: Qualified name of the top-level part usage.
            target_options: Key/value pairs for the LF target header.
            external: Optional foreign artifacts that implement external
                ``calc def`` calls. Artifacts are chosen based on the specified
                target language.
            strict_extern: Unused.
        """
        return build_part_program(
            model,
            usage_qn,
            target_options=target_options,
            external=self._own(external),
            target=self._new_target(),
        )

    @override
    def serialize(self, artifact: object, fmt: str) -> str:
        """Serialize the Lingua Franca program to the requested format."""
        if not isinstance(artifact, LfProgram):
            raise SerializationError("expected an LfProgram artifact")
        if fmt == "lf":
            return to_lf(artifact)
        raise SerializationError(f"unsupported format: {fmt!r}")

    def generate_output(
        self, artifact: object, options: OutputOptions
    ) -> GeneratedOutput:
        """Return the files :meth:`write` would write, without writing them.

        The program is ``<basename>.lf`` (``.ulf`` on uC target), followed
        by its companion types file: the Python module
        ``<types_module_name>.py`` when the program has one, or the C
        targets' header.

        Args:
            artifact: The program returned by :meth:`build`.
            options: The formats and the base filename; the basename
                defaults to the reactor name. ``output_dir`` is not used.

        Returns:
            The generated files, with paths relative to the output directory.

        Raises:
            SerializationError: If ``artifact`` is not an
                :class:`~sysmlc_rosetta.program.LfProgram`, or a format other
                than ``"lf"`` is requested.
        """
        if not isinstance(artifact, LfProgram):
            raise SerializationError("expected an LfProgram artifact")
        formats = options.formats or tuple(self.formats())
        for fmt in formats:
            if fmt != "lf":
                raise SerializationError(f"unsupported format: {fmt!r}")
        basename = options.basename or artifact.reactor.name
        extension = TARGETS[artifact.target].extension
        program = GeneratedFile(
            PurePosixPath(f"{basename}.{extension}"),
            self.serialize(artifact, "lf"),
        )
        companions = [
            GeneratedFile(PurePosixPath(name), text)
            for name, text in artifact.support_files
        ]
        if artifact.types_module_lines:
            assert artifact.types_module_name is not None
            companions.append(
                GeneratedFile(
                    PurePosixPath(f"{artifact.types_module_name}.py"),
                    "\n".join(artifact.types_module_lines) + "\n",
                )
            )
        return GeneratedOutput((program, *companions))

    @override
    def write(self, artifact: object, options: OutputOptions) -> list[Path]:
        """Write the files from :meth:`generate_output`.

        Args:
            artifact: The program returned by :meth:`build`.
            options: Where to write, the formats, and the base filename.

        Returns:
            The written paths: the program, then its types module if any.
        """
        return self.generate_output(artifact, options).write(options.output_dir)

    @override
    def summary(self, artifact: object) -> str:
        """Return a one-line description of the built program."""
        if not isinstance(artifact, LfProgram):
            return self.name
        reactor = artifact.reactor
        modes = len(reactor.modes)
        reactions = (
            len(reactor.reactions)
            + sum(len(mode.reactions) for mode in reactor.modes)
            + len(reactor.trailing_reactions)
        )
        return (
            f"LF program {reactor.name!r}: {modes} modes, {reactions} reactions"
        )
