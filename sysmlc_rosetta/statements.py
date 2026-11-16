"""Reaction bodies as a small statement tree.

The builder assembles reaction bodies from these nodes; a target
(:class:`sysmlc_rosetta.targets.base.LfTarget`) spells every leaf and turns
the tree into lines of its language. Passes that need to know what a body
does (which LF names it writes, which attributes it assigns) read the
nodes' metadata instead of searching rendered text.

A plain ``str`` in a body is one verbatim line in the target's language.
Callers outside rosetta (for example, a harness that prepends a ``print``)
may still add such lines while a program is being built.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence


@dataclass(frozen=True)
class Stmt:
    """One statement, already spelled in the target's language.

    Attributes:
        text: The statement's source, one line.
        writes: LF names the statement sets or schedules: output ports,
            contained-reactor inputs, logical actions.
        assigns: Bound root attributes the statement assigns directly (a
            chained target such as ``pt.x`` is not recorded).
        assigns_through: Bound root attributes whose field the statement
            assigns through a chained target (``pt`` for ``pt.x``). Kept
            apart from ``assigns``, which drives change notifications;
            asserted-constraint checks trigger on both.
        local: ``(type, name)`` of the local the statement assigns, for a
            language that declares locals: the first assignment in a block
            and its enclosing blocks becomes the declaration.
    """

    text: str
    writes: frozenset[str] = field(default_factory=frozenset)
    assigns: frozenset[str] = field(default_factory=frozenset)
    assigns_through: frozenset[str] = field(default_factory=frozenset)
    local: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if "\n" in self.text:
            raise ValueError(f"statement spans several lines: {self.text!r}")


@dataclass(frozen=True)
class If:
    """A first-match conditional: ``if``, any ``elif``s, optional ``else``.

    Attributes:
        branches: ``(condition, body)`` pairs in priority order; the
            condition is an expression in the target's language.
        orelse: The body run when no condition holds, or None.
    """

    branches: tuple[tuple[str, Body], ...]
    orelse: Body | None = None

    def __post_init__(self) -> None:
        if not self.branches:
            raise ValueError("a conditional needs at least one branch")


@dataclass(frozen=True)
class Pass:
    """An explicit no-op statement (Python ``pass``; nothing in C)."""


type Node = str | Stmt | If | Pass
type Body = tuple[Node, ...]


def iter_statements(body: Body) -> Iterator[Stmt]:
    """Yield every statement in ``body``, depth first, in source order."""
    for node in body:
        if isinstance(node, Stmt):
            yield node
        elif isinstance(node, If):
            for _condition, branch in node.branches:
                yield from iter_statements(branch)
            if node.orelse is not None:
                yield from iter_statements(node.orelse)


def writes(body: Body) -> frozenset[str]:
    """Return every LF name some statement in ``body`` writes."""
    return frozenset(
        name for stmt in iter_statements(body) for name in stmt.writes
    )


def insert_after_assignments(
    body: Body, names: frozenset[str], node: Node
) -> tuple[Body, bool]:
    """Insert ``node`` right after each statement assigning one of ``names``.

    The node lands in the same block as the assignment, so it runs only when
    the assignment does.

    Args:
        body: The body to rewrite.
        names: Attribute names whose assignment triggers an insertion.
        node: The node to insert.

    Returns:
        The rewritten body and whether anything was inserted.
    """
    return _insert_after(
        body, lambda stmt: (node,) if stmt.assigns & names else ()
    )


def insert_checks_after_writes(
    body: Body, checks: Sequence[tuple[Node, frozenset[str]]]
) -> tuple[Body, bool]:
    """Insert checks right after every write to an attribute they mention.

    After a statement that assigns an attribute, directly or through a
    chained target, every check mentioning it lands in the same block, in
    the order of ``checks`` and each once: a statement writing two
    attributes of one constraint gets that constraint's check once.

    Args:
        body: The body to rewrite.
        checks: Each check with the attribute names it mentions.

    Returns:
        The rewritten body and whether anything was inserted.
    """
    return _insert_after(
        body,
        lambda stmt: [
            check
            for check, names in checks
            if names & (stmt.assigns | stmt.assigns_through)
        ],
    )


def _insert_after(
    body: Body, due: Callable[[Stmt], Sequence[Node]]
) -> tuple[Body, bool]:
    """Insert ``due(stmt)`` right after each statement, in its block."""
    out: list[Node] = []
    changed = False
    for item in body:
        if isinstance(item, If):
            branches: list[tuple[str, Body]] = []
            for condition, branch in item.branches:
                new_branch, inner = _insert_after(branch, due)
                branches.append((condition, new_branch))
                changed |= inner
            orelse = item.orelse
            if orelse is not None:
                orelse, inner = _insert_after(orelse, due)
                changed |= inner
            out.append(replace(item, branches=tuple(branches), orelse=orelse))
            continue
        out.append(item)
        if isinstance(item, Stmt):
            nodes = due(item)
            out.extend(nodes)
            changed |= bool(nodes)
    return tuple(out), changed


def nest(condition: str, body: Body) -> If:
    """Return ``body`` guarded by ``condition``."""
    return If(((condition, tuple(body)),))


def stored(body: Body) -> tuple[str, ...]:
    """Store a statement tree in a reaction while its program is built.

    A built program's reaction bodies are lines of text, and that is the
    type consumers see. Until :func:`sysmlc_rosetta.builder.render_reactor`
    renders them, the builder keeps statement trees in the same field.
    """
    return cast("tuple[str, ...]", body)
