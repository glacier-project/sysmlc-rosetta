"""Generation-time width analysis of fixed-width numeric expressions.

One walk over a rendered top-level expression gives each node its math
range (by interval arithmetic) and its C evaluation type (C's integer
promotions and usual arithmetic conversions, with the store cast applied).
From those the analysis warns, the same way on every target, when a value
stored into a fixed-width feature may not fit it (rule 1), when an
arithmetic node may overflow the C type it is computed in (rule 2), and
when C would compare a possibly negative value as ``uint64_t``. The same
walk tells the C code generator which leaves get the store cast, which
comparisons are widened to ``int64_t``, which ``abs``/``min``/``max`` calls
use integer code and which real literals are single precision, so the
generator never re-derives these rules.

A feature's range is its kind's, narrowed by the literal bounds of the
machine's asserted constraints: those are checked at startup and right
after every write, so the narrowed range holds for the whole run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import reduce
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote, urlsplit

import syside
from sysmlc.codegen.expression import COMPARISON_OPERATORS
from sysmlc.errors import UnsupportedConstructError

from sysmlc_rosetta.c_types import scalar_type
from sysmlc_rosetta.fixed_width import (
    FIXED_WIDTH,
    FixedWidth,
    Interval,
    fixed_width_of,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from sysmlc.semantics.statemachine.facts import ConstraintFact

logger = logging.getLogger(__name__)

_INT: Final = FIXED_WIDTH["int32"]
_LONG_LONG: Final = FIXED_WIDTH["int64"]
_UNSIGNED: Final = FIXED_WIDTH["uint32"]
_UNSIGNED_LONG_LONG: Final = FIXED_WIDTH["uint64"]
_FLOAT32: Final = FIXED_WIDTH["float32"]
_FLOAT64: Final = FIXED_WIDTH["float64"]
# A plain ``Real`` (or quantity) value or a ``/`` result: a C double that is
# not a ``float64`` feature; the float kind rules tell them apart.
_REAL: Final = FixedWidth("Real", 64, "double", None)
# A real literal, or arithmetic of real literals and integers only: it
# takes the float kind of what it is combined with or stored into.
_REAL_LITERAL: Final = FixedWidth("real literal", 64, "double", None)

_ARITHMETIC: Final = frozenset(
    {
        syside.Operator.Plus,
        syside.Operator.Minus,
        syside.Operator.Multiply,
        syside.Operator.Modulo,
    }
)
_CONJUNCTIONS: Final = frozenset(
    {syside.Operator.And, syside.Operator.LogicalAnd}
)
# Each ordering and its form with the operands swapped.
_MIRRORED: Final = {
    syside.Operator.Less: syside.Operator.Greater,
    syside.Operator.LessEqual: syside.Operator.GreaterEqual,
    syside.Operator.Greater: syside.Operator.Less,
    syside.Operator.GreaterEqual: syside.Operator.LessEqual,
}
_INTEGER_FUNCTIONS: Final = frozenset(
    f"NumericalFunctions::{name}" for name in ("abs", "max", "min")
)

#: The C type both operands of a widened comparison are cast to.
WIDE_COMPARISON: Final = _LONG_LONG.ctype


@dataclass(frozen=True)
class Evaluation:
    """One walk over a top-level expression.

    Nodes are Syside expression nodes, which compare and hash by identity
    of the model element, so a code generator looks a node up while it
    emits it.

    Attributes:
        kinds: The C evaluation type of every numeric node: a promoted
            integer kind or a float kind. A non-numeric or unknown node is
            absent.
        cast_leaves: The integer leaves (literals, references, chains) the
            store cast widens to the destination's C type (their kind).
        widened: The comparisons whose two operands are cast to
            :data:`WIDE_COMPARISON`: a ``uint32_t`` against a signed
            integer, which C would otherwise compare as unsigned.
        integer_calls: The ``abs``/``min``/``max`` calls computed in their
            integer kind (not through ``<math.h>``'s doubles): every
            argument is an integer and one involves a fixed-width integer
            feature.
        single: The real literals spelled single precision (``0.25f``):
            combined only with ``float32`` values, or stored into one.
        warnings: The width warnings, in order.

    The default is the evaluation of no expression.
    """

    kinds: Mapping[syside.Expression, FixedWidth] = field(default_factory=dict)
    cast_leaves: frozenset[syside.Expression] = frozenset()
    widened: frozenset[syside.Expression] = frozenset()
    integer_calls: frozenset[syside.Expression] = frozenset()
    single: frozenset[syside.Expression] = frozenset()
    warnings: tuple[str, ...] = ()

    def kind(self, node: syside.Expression) -> FixedWidth | None:
        """The C evaluation type of ``node``; None if not numeric."""
        return self.kinds.get(node)


def literal_bounds(
    constraints: Sequence[ConstraintFact],
) -> dict[syside.Feature, Interval]:
    """The ranges asserted constraints give fixed-width integer features.

    Only a non-negated constraint narrows, and only through its parts
    ``attr <op> literal`` and ``literal <op> attr`` (an ordering against an
    integer literal, optionally negated), alone or joined by ``and``. A
    bound against another attribute does not narrow: ``--values`` can
    change that attribute after generation.

    Args:
        constraints: The machine's root-scope asserted constraints.

    Returns:
        Each bounded feature's range, inside its kind's.
    """
    bounds: dict[syside.Feature, Interval] = {}
    for fact in constraints:
        if fact.is_negated:
            continue
        for part in _conjuncts(fact.expression):
            _narrow(bounds, part)
    return bounds


class WidthAnalysis:
    """Width evaluation and warnings for the expressions a program renders.

    Attributes:
        bounds: Literal bounds of fixed-width features, as from
            :func:`literal_bounds`; a builder adds its machine's.
        warnings: The warnings found so far, in order and without repeats.
    """

    def __init__(self) -> None:
        self.bounds: dict[syside.Feature, Interval] = {}
        self.warnings: dict[str, None] = {}

    def check(self, expr: syside.Expression) -> Evaluation:
        """Evaluate a rendered top-level expression; record its warnings.

        An expression that is the value of a fixed-width feature (an
        assignment's value, an initial or default value, a payload field)
        is evaluated as a store into it.
        """
        destination = stored_into(expr)
        dest = None if destination is None else fixed_width_of(destination)
        where = _name(expr if destination is None else destination)
        evaluation = self.evaluate(expr, dest, where)
        self.warnings.update(dict.fromkeys(evaluation.warnings))
        return evaluation

    def evaluate(
        self, expr: syside.Expression, dest: FixedWidth | None, where: str
    ) -> Evaluation:
        """Evaluate ``expr``, stored into ``dest`` unless that is None.

        Args:
            expr: The top-level expression.
            dest: The fixed-width kind it is stored into, if any.
            where: The qualified name warnings name: the destination's, or
                that of the element owning ``expr``.
        """
        return _Walk(self.bounds, expr, dest, where).result()

    def report(self) -> None:
        """Log every recorded warning once."""
        for message in self.warnings:
            logger.warning("%s", message)


@dataclass(frozen=True)
class _Value:
    """A node's math range (None: unknown) and C kind (None: not numeric).

    ``overflowed`` is the C kind whose overflow rule 2 already reported at
    or below the node: a node above computed in that same kind carries the
    overflow and does not repeat the warning. ``fixed`` tells whether the
    node involves a fixed-width integer feature.
    """

    range: Interval | None
    kind: FixedWidth | None
    overflowed: FixedWidth | None = None
    fixed: bool = False


@dataclass
class _Walk:
    """The state of one evaluation; see :class:`Evaluation`."""

    bounds: Mapping[syside.Feature, Interval]
    top: syside.Expression
    dest: FixedWidth | None
    where: str
    kinds: dict[syside.Expression, FixedWidth] = field(default_factory=dict)
    cast_leaves: set[syside.Expression] = field(default_factory=set)
    widened: set[syside.Expression] = field(default_factory=set)
    integer_calls: set[syside.Expression] = field(default_factory=set)
    # Each real literal and the root of the arithmetic or comparison it is
    # part of; a real comparison's float kind.
    literal_groups: dict[syside.Expression, syside.Expression] = field(
        default_factory=dict
    )
    compared: dict[syside.Expression, FixedWidth] = field(default_factory=dict)
    found: list[str] = field(default_factory=list)

    def result(self) -> Evaluation:
        """Walk the expression; apply the store rules to its value.

        C computes everything narrower than ``int`` in ``int``, so only an
        integer destination of rank at least ``int`` gets the store cast.
        """
        dest = self.dest
        cast = dest if dest is not None and dest.rank_at_least_int else None
        value = self._value(self.top, cast, None)
        kind = value.kind
        if dest is not None and not dest.is_float and kind is not None:
            if kind.is_float:
                self._warn(self.top, f"is real, stored in {dest.name}")
            elif value.range is not None and not value.range.fits(dest):
                needs = f"needs {value.range.lo}..{value.range.hi}"
                self._warn(self.top, f"{needs}, stored in {dest.name}")
        elif dest == _FLOAT32 and kind == _FLOAT64:
            self._warn(self.top, "is float64, stored in float32")
        single = frozenset(
            literal
            for literal, root in self.literal_groups.items()
            if self.kinds.get(root, self.compared.get(root)) == _FLOAT32
            or (
                root == self.top
                and dest == _FLOAT32
                and self.kinds.get(root) == _REAL_LITERAL
            )
        )
        return Evaluation(
            dict(self.kinds),
            frozenset(self.cast_leaves),
            frozenset(self.widened),
            frozenset(self.integer_calls),
            single,
            tuple(self.found),
        )

    def _value(
        self,
        expr: syside.Expression,
        cast: FixedWidth | None,
        group: syside.Expression | None,
    ) -> _Value:
        """Evaluate a node and record its kind.

        Args:
            expr: The node.
            cast: The store cast's kind, while it still applies.
            group: The root of the arithmetic ``expr`` is an operand of, if
                any (for the float kind of real literals).
        """
        value = self._dispatch(expr, cast, group)
        if value.kind is not None:
            self.kinds[expr] = value.kind
        return value

    def _dispatch(
        self,
        expr: syside.Expression,
        cast: FixedWidth | None,
        group: syside.Expression | None,
    ) -> _Value:
        """Branch order follows the code generators' dispatch."""
        if isinstance(expr, syside.LiteralRational):
            self.literal_groups[expr] = group or expr
            return _Value(None, _REAL_LITERAL)
        if isinstance(expr, syside.LiteralInteger):
            value = _integer(expr)
            span = Interval(value, value)
            return _Value(
                span, self._leaf(expr, span, _literal_kind(value), cast)
            )
        literal = negated_literal(expr)
        if literal is not None:
            value = -_integer(literal)
            span = Interval(value, value)
            return _Value(
                span, self._leaf(expr, span, _negated_kind(literal), cast)
            )
        if isinstance(expr, syside.FeatureChainExpression):
            # The source is evaluated for its literals; a chain's value is
            # its target feature's.
            self._value(expr.operands.collect()[0], None, None)
            target = expr.target_feature
            if target is not None:
                target = (target.chaining_features.collect() or [target])[-1]
            return self._feature(expr, target, cast)
        if isinstance(expr, syside.OperatorExpression):
            return self._operator(expr, cast, group)
        if isinstance(expr, syside.InvocationExpression):
            return self._invocation(expr)
        if isinstance(expr, syside.FeatureReferenceExpression):
            return self._feature(expr, expr.referent, cast)
        if isinstance(expr, syside.InstantiationExpression):
            for argument in expr.arguments.collect():
                self._value(argument, None, None)
        return _Value(None, None)

    def _leaf(
        self,
        expr: syside.Expression,
        own: Interval,
        kind: FixedWidth,
        cast: FixedWidth | None,
    ) -> FixedWidth:
        """An integer leaf's kind: the store cast only ever widens.

        The leaf takes the cast's kind when its own values (a literal's
        value, a feature's whole kind) all fit it, and is a cast leaf when
        that changes its promoted kind; else it keeps its own and C's usual
        conversions apply.
        """
        promoted = _promote(kind)
        if cast is None or not own.fits(cast):
            return promoted
        if promoted != cast:
            self.cast_leaves.add(expr)
        return cast

    def _feature(
        self,
        expr: syside.Expression,
        feature: syside.Feature | None,
        cast: FixedWidth | None,
    ) -> _Value:
        """A reference: a fixed-width range, or a plain kind with none."""
        if feature is None:
            return _Value(None, None)
        fixed = fixed_width_of(feature)
        kind = _plain_kind(feature) if fixed is None else fixed
        if kind is None or kind.range is None:
            return _Value(None, kind)
        leaf = self._leaf(expr, kind.range, kind, cast)
        if fixed is None:
            return _Value(None, leaf)
        return _Value(self.bounds.get(feature, kind.range), leaf, fixed=True)

    def _operator(
        self,
        expr: syside.OperatorExpression,
        cast: FixedWidth | None,
        group: syside.Expression | None,
    ) -> _Value:
        """Arithmetic keeps the store cast; anything else stops it.

        ``/`` (real division) continues the float grouping of real
        literals and a comparison starts one; a comparison or a logical
        operator yields no number.
        """
        op = expr.operator
        operands = expr.operands.collect()
        arithmetic = op in _ARITHMETIC and len(operands) in {1, 2}
        real = op is syside.Operator.Divide
        comparison = op in COMPARISON_OPERATORS and len(operands) == 2
        inner = (group or expr) if arithmetic or real else None
        values = [
            self._value(
                operand,
                cast if arithmetic else None,
                expr if comparison else inner,
            )
            for operand in operands
        ]
        if real:
            return _Value(None, _real_kind([v.kind for v in values]))
        if comparison:
            self._compare(expr, values)
        if not arithmetic:
            return _Value(None, None)
        divisor = _literal(operands[1]) if len(operands) == 2 else None
        return self._numeric(
            expr, values, lambda ranges: _arithmetic(op, ranges, divisor)
        )

    def _invocation(self, expr: syside.InvocationExpression) -> _Value:
        """A call stops the store cast and the literal grouping.

        Integer ``abs``/``min``/``max`` follow their math, and use integer
        code when an argument involves a fixed-width integer feature (else
        C keeps ``<math.h>``'s functions, as without fixed-width types);
        other calls give an unknown value.
        """
        values = [
            self._value(argument, None, None)
            for argument in expr.arguments.collect()
        ]
        function = expr.function
        name = "" if function is None else str(function.qualified_name)
        if name not in _INTEGER_FUNCTIONS:
            return _Value(None, None)
        value = self._numeric(
            expr,
            values,
            lambda ranges: _numerical(name, ranges),
            ordering=not name.endswith("::abs"),
        )
        if value.fixed and value.kind is not None and not value.kind.is_float:
            self.integer_calls.add(expr)
        return value

    def _numeric(
        self,
        expr: syside.Expression,
        values: Sequence[_Value],
        span: Callable[[Sequence[Interval]], Interval | None],
        *,
        ordering: bool = False,
    ) -> _Value:
        """An arithmetic node's value from its operands' values.

        An integer ``min``/``max`` (``ordering``) compares its arguments,
        so a mixed-sign one is computed as :meth:`_mixed_sign` says.

        Rule 2: warn when its range does not fit the type C computes it
        in, unless an operand computed in the same type already overflowed
        (the warning there is the cause), or the node is the root of a
        store whose cast gives it the destination's type (the store's own
        warning says it).
        """
        kinds = [value.kind for value in values]
        if not values or None in kinds:
            return _Value(None, None)
        kind = reduce(_convert, [k for k in kinds if k is not None], _INT)
        fixed = any(value.fixed for value in values)
        if ordering and fixed and self._mixed_sign(expr, values, kind):
            kind = _LONG_LONG
        ranges = [value.range for value in values]
        if kind.is_float or None in ranges:
            return _Value(None, kind, fixed=fixed)
        result = span([r for r in ranges if r is not None])
        if any(value.overflowed == kind for value in values):
            return _Value(result, kind, overflowed=kind, fixed=fixed)
        if result is None or result.fits(kind):
            return _Value(result, kind, fixed=fixed)
        if expr != self.top or kind != self.dest:
            needs = f"needs {result.lo}..{result.hi}"
            self._warn(expr, f"{needs}, computed in {kind.ctype}")
        return _Value(result, kind, overflowed=kind, fixed=fixed)

    def _compare(
        self, expr: syside.Expression, values: Sequence[_Value]
    ) -> None:
        """The type C compares two numbers in.

        A real comparison records its float kind (for its real literals);
        a mixed-sign integer one is widened as :meth:`_mixed_sign` says.
        """
        kinds = [value.kind for value in values]
        if None in kinds:
            return
        common = reduce(_convert, [k for k in kinds if k is not None])
        if common.is_float:
            self.compared[expr] = common
        elif self._mixed_sign(expr, values, common):
            self.widened.add(expr)

    def _mixed_sign(
        self,
        expr: syside.Expression,
        values: Sequence[_Value],
        common: FixedWidth,
    ) -> bool:
        """Whether a comparison of ``values`` is widened to ``int64_t``.

        C converts a signed operand to the unsigned type ``common``, so
        ``-1`` compares above ``1u``. ``int64_t`` holds every ``uint32_t``
        and signed ``int`` value, so such a comparison is computed in it;
        against ``uint64_t`` no wider type exists, so a signed operand that
        may be negative warns.
        """
        signed = [v for v in values if v.kind is not None and v.kind.is_signed]
        if common.is_float or common.is_signed or not signed:
            return False
        if common == _UNSIGNED:
            return True
        for value in signed:
            if value.range is None or value.range.lo < 0:
                assert value.kind is not None
                self._warn(
                    expr,
                    "compares uint64_t with a possibly negative "
                    f"{value.kind.ctype}",
                )
        return False

    def _warn(self, expr: syside.Expression, problem: str) -> None:
        self.found.append(_message(self.where, expr, problem))


def negated_literal(expr: syside.Expression) -> syside.LiteralInteger | None:
    """The integer literal a unary minus applies to directly, if any.

    ``-<literal>`` is one signed literal, of the C type of its magnitude;
    ``-9223372036854775808`` is ``INT64_MIN`` (the magnitude alone would be
    ``uint64_t``).
    """
    if (
        isinstance(expr, syside.OperatorExpression)
        and expr.operator is syside.Operator.Minus
    ):
        operands = expr.operands.collect()
        if len(operands) == 1 and isinstance(
            operands[0], syside.LiteralInteger
        ):
            return operands[0]
    return None


def stored_into(expr: syside.Expression) -> syside.Feature | None:
    """The feature a top-level expression is the value of, if any.

    That is an assignment's target, or the feature whose initial, default
    or bound value ``expr`` is (an attribute, an item field, a payload
    constructor argument, which redefines the field it binds).
    """
    owner = expr.owner
    if not (
        isinstance(owner, syside.Feature)
        and owner.feature_value_expression == expr
    ):
        return None
    assignment = owner.owner
    if isinstance(assignment, syside.AssignmentActionUsage):
        return (
            assignment.referent if assignment.value_expression == expr else None
        )
    return owner


def _promote(kind: FixedWidth) -> FixedWidth:
    """C's integer promotion: integers narrower than ``int`` become it."""
    return _INT if not kind.is_float and kind.bits < _INT.bits else kind


def _convert(left: FixedWidth, right: FixedWidth) -> FixedWidth:
    """The type C computes a binary operation of two values in.

    A float operand makes the operation real (:func:`_real_kind`).
    Otherwise C's usual arithmetic conversions on the promoted operands:
    same signedness, the wider; an unsigned at least as wide as the
    signed, the unsigned; else the signed (which then holds every value
    of the unsigned).
    """
    if left.is_float or right.is_float:
        return _real_kind([left, right])
    left, right = _promote(left), _promote(right)
    if left.is_signed == right.is_signed:
        return left if left.bits >= right.bits else right
    unsigned, signed = (right, left) if left.is_signed else (left, right)
    return unsigned if unsigned.bits >= signed.bits else signed


def _real_kind(kinds: Sequence[FixedWidth | None]) -> FixedWidth:
    """The float kind of a real operation.

    ``float64`` if an operand is, else ``float32`` if one is, else a plain
    real if one is, else a real literal's; the real division of two
    integers is a plain real.
    """
    for kind in (_FLOAT64, _FLOAT32, _REAL, _REAL_LITERAL):
        if kind in kinds:
            return kind
    return _REAL


def _plain_kind(feature: syside.Feature) -> FixedWidth | None:
    """A plain feature's kind, from its C mapping (``int`` or ``double``).

    A scalar quantity value is a real.
    """
    for definition in feature.types.collect():
        ctype = scalar_type(definition)
        if ctype == _INT.ctype:
            return _INT
        if ctype == _REAL.ctype:
            return _REAL
    return None


def _literal_kind(value: int) -> FixedWidth:
    """The type C gives a decimal literal.

    The first of ``int`` and ``long long`` that holds it; above those, the
    generated C spells it ``UINT64_C(...)``.
    """
    for kind in (_INT, _LONG_LONG):
        if kind.holds(value):
            return kind
    return _UNSIGNED_LONG_LONG


def _negated_kind(literal: syside.LiteralInteger) -> FixedWidth:
    """The C type of ``-<literal>`` (see :func:`negated_literal`).

    The type of the magnitude, except ``INT64_MIN``'s. A value below
    ``INT64_MIN`` keeps its true range (C cannot spell it; the C generator
    rejects it), so a fixed-width store of it warns.
    """
    magnitude = _integer(literal)
    kind = _literal_kind(magnitude)
    if kind == _UNSIGNED_LONG_LONG and _LONG_LONG.holds(-magnitude):
        return _LONG_LONG
    return kind


def _integer(expr: syside.LiteralInteger) -> int:
    """The value of an integer literal, which must fit in 64 bits.

    Syside stores a literal above ``UINT64_MAX`` as ``UINT64_MAX`` (with a
    load warning), so no target could emit the model's value.

    Raises:
        UnsupportedConstructError: If the source literal is above
            ``UINT64_MAX``.
    """
    cst = expr.cst_node
    text = "" if cst is None else cst.text()
    if text.isdigit() and not _UNSIGNED_LONG_LONG.holds(int(text)):
        raise UnsupportedConstructError(
            f"integer literal {text} does not fit in 64 bits", node=expr
        )
    return expr.value


def _literal(expr: syside.Expression) -> int | None:
    """The value of an integer literal, optionally negated; else None."""
    if isinstance(expr, syside.LiteralInteger):
        return _integer(expr)
    literal = negated_literal(expr)
    return None if literal is None else -_integer(literal)


def _arithmetic(
    op: syside.Operator, ranges: Sequence[Interval], divisor: int | None
) -> Interval | None:
    """Interval arithmetic for unary ``+ -`` and binary ``+ - * %``.

    ``%`` is bounded only by a literal divisor ``d``: below ``|d|``, and
    non-negative for a non-negative dividend (C and Python agree there).
    """
    if len(ranges) == 1:
        (arg,) = ranges
        if op is syside.Operator.Minus:
            return Interval(-arg.hi, -arg.lo)
        return arg
    left, right = ranges
    if op is syside.Operator.Modulo:
        if not divisor:
            return None
        top = abs(divisor) - 1
        return Interval(0 if left.lo >= 0 else -top, top)
    if op is syside.Operator.Plus:
        return Interval(left.lo + right.lo, left.hi + right.hi)
    if op is syside.Operator.Minus:
        return Interval(left.lo - right.hi, left.hi - right.lo)
    products = [a * b for a in (left.lo, left.hi) for b in (right.lo, right.hi)]
    return Interval(min(products), max(products))


def _numerical(name: str, ranges: Sequence[Interval]) -> Interval:
    """The range of an integer ``abs``, ``min`` or ``max``."""
    if name.endswith("::min"):
        return Interval(min(r.lo for r in ranges), min(r.hi for r in ranges))
    if name.endswith("::max"):
        return Interval(max(r.lo for r in ranges), max(r.hi for r in ranges))
    (arg,) = ranges
    if arg.lo >= 0:
        return arg
    if arg.hi <= 0:
        return Interval(-arg.hi, -arg.lo)
    return Interval(0, max(-arg.lo, arg.hi))


def _conjuncts(expr: syside.Expression) -> Iterator[syside.Expression]:
    """The parts of an ``and`` chain; any other expression is one part."""
    if (
        isinstance(expr, syside.OperatorExpression)
        and expr.operator in _CONJUNCTIONS
    ):
        for operand in expr.operands.collect():
            yield from _conjuncts(operand)
    else:
        yield expr


def _narrow(
    bounds: dict[syside.Feature, Interval], part: syside.Expression
) -> None:
    """Narrow a feature's range by one ``attr <op> literal`` part."""
    if not (
        isinstance(part, syside.OperatorExpression)
        and part.operator in _MIRRORED
    ):
        return
    left, right = part.operands.collect()
    op = part.operator
    if isinstance(right, syside.FeatureReferenceExpression):
        left, right, op = right, left, _MIRRORED[op]
    value = _literal(right)
    feature = (
        left.referent
        if isinstance(left, syside.FeatureReferenceExpression)
        else None
    )
    fixed = None if feature is None else fixed_width_of(feature)
    if value is None or feature is None or fixed is None or fixed.range is None:
        return
    current = bounds.get(feature, fixed.range)
    lo, hi = current.lo, current.hi
    if op is syside.Operator.Less:
        hi = min(hi, value - 1)
    elif op is syside.Operator.LessEqual:
        hi = min(hi, value)
    elif op is syside.Operator.Greater:
        lo = max(lo, value + 1)
    else:
        lo = max(lo, value)
    bounds[feature] = Interval(lo, hi)


def _message(where: str, expr: syside.Expression, problem: str) -> str:
    """``<where>: <source> <problem>``, then the source location if known."""
    text = f"{where}: {syside.pprint(expr).strip()} {problem}"
    cst = expr.cst_node
    document = expr.document
    if cst is None or document is None:
        return text
    path = unquote(urlsplit(str(document.url)).path)
    point = cst.start_point
    return f"{text} ({path}:{point.line + 1}:{point.character + 1})"


def _name(element: syside.Element) -> str:
    """The element's qualified name, else its name under its named owner."""
    qualified = element.qualified_name
    if qualified is not None:
        return str(qualified)
    owner = element.owner
    outer = "<anonymous>" if owner is None else _name(owner)
    return outer if element.name is None else f"{outer}::{element.name}"
