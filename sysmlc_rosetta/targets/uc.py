"""LF's reactor-uc runtime with micromode modal reactors (``.ulf`` sources).

Reaction bodies are the C target's, but for a few runtime calls. 

The program shape differs from reactor-c's in these ways:
- there is no ``target`` declaration: the main reactor carries the run
  options as attributes (``@platform("native")``, ``@timeout``, ``@fast``)
  and ``@logging("ERROR")``, which keeps the runtime as quiet as reactor-c;
- micromode rejects a reaction triggered by both ``startup`` and
  ``reset``, so :meth:`UcTarget.adapt` splits each mode's entry reaction;
- only a mode's own reactions may refer to the reactors it contains, so
  the builder keeps a composite state's drain inside its mode
  (:attr:`~sysmlc_rosetta.targets.base.LfTarget.mode_local_children`);
- reactor-uc has no ``string`` type: ``current_state`` is a
  ``const char*`` set only from string literals;
- each logical action has a fixed event buffer, sized by
  :meth:`UcTarget.declare`;
- the native build adds no source files and links no math library: an
  external C source is compiled through the main reactor's preamble, and
  ``abs``, ``max`` and ``min`` call the :data:`MATH_HELPERS` instead of
  ``<math.h>`` (see :class:`~sysmlc_rosetta.c_codegen.LfUcCodeGen`).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Final, override

from sysmlc.codegen.c import c_string
from sysmlc.errors import SerializationError

from sysmlc_rosetta.lifecycle import ENTRY_TRIGGERS, OUTPUT_PORT
from sysmlc_rosetta.program import StateVar
from sysmlc_rosetta.statements import Stmt, iter_statements, nest
from sysmlc_rosetta.targets.c import CTarget

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sysmlc_rosetta.codegen import PreambleNeeds
    from sysmlc_rosetta.program import (
        LfProgram,
        LogicalAction,
        Mode,
        Reaction,
        Reactor,
    )

#: The tag at which a reactor last ran a mode's entry.
ENTERED_VAR: Final = "_entered"

#: SysML library functions spelled without ``<math.h>``: each function's
#: qualified name, mapped to its C callee and the callee's definition.
MATH_HELPERS: Final = {
    "NumericalFunctions::abs": (
        "sysmlc_abs",
        "static inline double sysmlc_abs(double x) { return x < 0 ? -x : x; }",
    ),
    "NumericalFunctions::max": (
        "sysmlc_max",
        "static inline double sysmlc_max(double x, double y) "
        "{ return x > y ? x : y; }",
    ),
    "NumericalFunctions::min": (
        "sysmlc_min",
        "static inline double sysmlc_min(double x, double y) "
        "{ return x < y ? x : y; }",
    ),
}

_MAIN_ATTRIBUTES: Final = {"timeout": "@timeout({})", "fast": "@fast({})"}


class UcTarget(CTarget):
    """Spell reaction bodies and programs for reactor-uc (micromode)."""

    name = "uc"
    lf_name = "uC"
    extension = "ulf"
    mode_local_children = True

    # -- program --

    @override
    def declare(self, reactor: Reactor) -> Reactor:
        """Declare as the C target does, and size each action's buffer.

        reactor-uc holds a fixed number of pending events per logical
        action, one by default. A statement schedules its action at most
        once per tag, and the event is taken at the next microstep or,
        mode-local, dropped when its mode is entered again, so an action
        holds as many events as statements schedule it. Events of one
        action at one tag reach its reactions as one occurrence with the
        last value (reactor-c delivers them a microstep apart).
        """
        declared = super().declare(reactor)
        reactions = (
            *reactor.reactions,
            *(r for mode in reactor.modes for r in mode.reactions),
            *reactor.trailing_reactions,
        )
        schedules = Counter(
            name
            for reaction in reactions
            for stmt in iter_statements(reaction.body)
            for name in stmt.writes
        )

        def sized(
            actions: tuple[LogicalAction, ...],
        ) -> tuple[LogicalAction, ...]:
            return tuple(
                replace(action, pending=max(1, schedules[action.name]))
                for action in actions
            )

        return replace(
            declared,
            actions=sized(declared.actions),
            modes=tuple(
                replace(mode, actions=sized(mode.actions))
                for mode in declared.modes
            ),
        )

    @override
    def port_type(self, port: str) -> str:
        if port.endswith(OUTPUT_PORT):
            return "const char*"
        return super().port_type(port)

    @override
    def preamble_lines(self, needs: PreambleNeeds) -> list[str]:
        lines = super().preamble_lines(needs)
        if needs.uses_math:
            lines += [definition for _, definition in MATH_HELPERS.values()]
        return lines

    @override
    def target_options(
        self, needs: PreambleNeeds
    ) -> tuple[tuple[str, str], ...]:
        """None: the build finds the files beside the program's source."""
        return ()

    @override
    def support_files(
        self, needs: PreambleNeeds
    ) -> tuple[tuple[str, str], ...]:
        return (
            self.types_header(needs),
            *((a.path.name, a.path.read_text()) for a in needs.external),
        )

    # -- program text --

    @override
    def header(self, options: tuple[tuple[str, str], ...]) -> list[str]:
        """None: a ``.ulf`` program declares no target."""
        return []

    @override
    def main_opening(self, program: LfProgram) -> list[str]:
        """Open the main reactor with its attributes and source preamble.

        Raises:
            SerializationError: If a target option has no main attribute.
        """
        lines = ['@platform("native")']
        for key, value in program.target_options:
            if key not in _MAIN_ATTRIBUTES:
                raise SerializationError(
                    f"the reactor-uc target has no attribute for {key!r}"
                )
            lines.append(_MAIN_ATTRIBUTES[key].format(value))
        lines += ['@logging("ERROR")', "main reactor {"]
        sources = [
            name for name, _ in program.support_files if name.endswith(".c")
        ]
        if sources:
            lines += [
                "  preamble {=",
                *(f'    #include "{name}"' for name in sources),
                "  =}",
            ]
        return lines

    @override
    def adapt(self, reactor: Reactor) -> Reactor:
        """Split every mode's entry reaction in a ``reset`` and a ``startup``.

        A mode is entered with ``reset``. An initial mode is also entered
        with ``startup`` when its reactor starts: alone at program start,
        together with ``reset`` when an enclosing mode is first entered.
        So only an initial mode gets a ``startup`` reaction, and both of
        its reactions run the entry once per tag, guarded by
        :data:`ENTERED_VAR`.
        """
        if not reactor.modes:
            return reactor
        guard = StateVar(ENTERED_VAR, "NEVER_TAG", reset=True, type="tag_t")
        return replace(
            reactor,
            state_vars=(*reactor.state_vars, guard),
            modes=tuple(
                replace(mode, reactions=tuple(self._entries(mode)))
                for mode in reactor.modes
            ),
        )

    def _entries(self, mode: Mode) -> Iterator[Reaction]:
        """The mode's reactions with its entry reaction split."""
        for reaction in mode.reactions:
            if reaction.triggers != ENTRY_TRIGGERS:
                yield reaction
            elif not mode.initial:
                yield replace(reaction, triggers=("reset",))
            else:
                now = self.arrival()
                entered = self.var(ENTERED_VAR)
                body = self.render(
                    (
                        nest(
                            f"lf_tag_compare({entered}, {now}) != 0",
                            (Stmt(f"{entered} = {now};"), *reaction.body),
                        ),
                    )
                )
                yield replace(reaction, triggers=("startup",), body=body)
                yield replace(reaction, triggers=("reset",), body=body)

    # -- harness observation, time and runtime calls --

    @override
    def is_present(self, port: str) -> str:
        return f"lf_is_present({port})"

    @override
    def arrival(self) -> str:
        return "env->scheduler->current_tag(env->scheduler)"

    @override
    def elapsed(self) -> str:
        return "env->get_elapsed_logical_time(env)"

    @override
    def schedule(
        self, action: str, delay: str = "0", value: str | None = None
    ) -> Stmt:
        if value is None:
            return super().schedule(action, delay)
        return Stmt(
            f"lf_schedule({action}, {delay}, ({value}));",
            writes=frozenset((action,)),
        )

    @override
    def fail(self, message: str) -> Stmt:
        return Stmt(f"throw({c_string(message)});")

    @override
    def request_stop(self) -> Stmt:
        return Stmt("env->request_shutdown(env, 0);")

    @override
    def log(self, message: str) -> Stmt:
        return Stmt(f'LF_DEBUG(ENV, "%s", {c_string(message)});')
