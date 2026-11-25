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
- the program is built through the ``CMakeLists.txt`` that
  :func:`cmake_lists` writes beside it: that file runs reactor-uc's
  compiler in generate-only mode, links the math library, and compiles
  external C sources as ordinary sources, so reaction bodies use
  ``<math.h>`` as the C target's do.
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
    from collections.abc import Iterator, Sequence

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

_MAIN_ATTRIBUTES: Final = {"timeout": "@timeout({})", "fast": "@fast({})"}


class UcTarget(CTarget):
    """Spell reaction bodies and programs for reactor-uc (micromode)."""

    name = "uc"
    lf_name = "uC"
    extension = "ulf"
    mode_local_children = True
    shared_state_namespace = True
    unqualified_port_references = True

    @override
    def context_state(self, slot: str, ctype: str | None) -> StateVar:
        """Use a compound literal for context assignment in uC constructors."""
        state = super().context_state(slot, ctype)
        return (
            replace(state, init=f"({ctype}){{0}}")
            if ctype is not None
            else state
        )

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


_CMAKE_LISTS: Final = """\
cmake_minimum_required(VERSION 3.20)
project({main} LANGUAGES C)

if(NOT DEFINED ENV{{REACTOR_UC_PATH}})
  message(FATAL_ERROR "set REACTOR_UC_PATH to a reactor-uc checkout")
endif()
set(SYSMLC_LFC "$ENV{{REACTOR_UC_PATH}}/ulf/build/install/lf-cli/bin/lfc")

# reactor-uc's compiler writes its own CMakeLists.txt (runtime, micromode and
# logging wiring) into src-gen/{main}; run it again when the program changes.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${{CMAKE_CURRENT_SOURCE_DIR}}/{main}.ulf")
execute_process(
  COMMAND "${{SYSMLC_LFC}}" -n "${{CMAKE_CURRENT_SOURCE_DIR}}/{main}.ulf"
  WORKING_DIRECTORY "${{CMAKE_CURRENT_SOURCE_DIR}}"
  RESULT_VARIABLE SYSMLC_LFC_RESULT)
if(NOT SYSMLC_LFC_RESULT EQUAL 0)
  message(FATAL_ERROR "lfc -n failed on {main}.ulf (${{SYSMLC_LFC_RESULT}})")
endif()

add_subdirectory("${{CMAKE_CURRENT_SOURCE_DIR}}/src-gen/{main}" lf)
target_link_libraries({main} PRIVATE m)
{sources}set_target_properties({main} PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${{CMAKE_CURRENT_SOURCE_DIR}}/bin")
"""


def cmake_lists(main: str, files: Sequence[str] = ()) -> str:
    """The top-level ``CMakeLists.txt`` for ``<main>.ulf`` beside it.

    The file wraps the build reactor-uc's compiler (``lfc -n``) generates
    under ``src-gen/<main>``: that build wires the runtime, the micromode
    extension and the ``@logging`` level, which reactor-uc's own
    ``lf_build_generated_code`` helper does not for modal programs. The
    wrapper only adds what rosetta's programs need on top: the math
    library, and the external C sources. Configure with ``REACTOR_UC_PATH``
    set to a reactor-uc checkout whose compiler is built; the executable
    lands in ``bin/<main>`` beside the program, where ``lfc`` itself puts
    it.

    The external C sources are compiled as ordinary sources of the
    program's target instead of being ``#include``-d in the main reactor's
    preamble: their headers stay included by the program's preamble, so no
    symbol is defined twice, and the sources get the build's own warnings
    and include path. The caller must then not include them elsewhere.

    Args:
        main: The main reactor's name, the ``.ulf`` file's stem (also the
            CMake target lfc defines); a valid C identifier, as lfc
            requires, and not a reactor's name up to case.
        files: The program's support and external files, as paths
            relative to the directory of the ``CMakeLists.txt`` (and the
            ``.ulf``); its ``.c`` sources are compiled into the program, in
            this order.

    Returns:
        The file's text.
    """
    sources = [name for name in files if name.endswith(".c")]
    listed = "".join(
        f'  "${{CMAKE_CURRENT_SOURCE_DIR}}/{source}"\n' for source in sources
    )
    block = f"target_sources({main} PRIVATE\n{listed})\n" if sources else ""
    return _CMAKE_LISTS.format(main=main, sources=block)
