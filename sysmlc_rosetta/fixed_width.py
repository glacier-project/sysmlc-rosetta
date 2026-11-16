"""Fixed-width numeric kinds from the ``FixedWidthScalarValues`` library.

Models subset the library's features (``attribute count :> uint8``).
An attribute has the kind of the library feature it specializes through
subsettings and redefinitions, matched by qualified name, so user
refinements and redefinitions are covered and a same-named feature in a
user package is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sysmlc.errors import UnsupportedConstructError

if TYPE_CHECKING:
    from collections.abc import Mapping

    import syside

_LIBRARY: Final = "FixedWidthScalarValues"


@dataclass(frozen=True)
class Interval:
    """An inclusive integer range ``lo..hi``."""

    lo: int
    hi: int

    def fits(self, kind: FixedWidth) -> bool:
        """Whether every value of the range is a value of integer ``kind``."""
        whole = kind.range
        return whole is not None and whole.lo <= self.lo and self.hi <= whole.hi


@dataclass(frozen=True)
class FixedWidth:
    """One fixed-width numeric kind.

    Attributes:
        name: Library feature name, such as ``uint8``.
        bits: Width in bits.
        ctype: C type, such as ``uint8_t``.
        range: The integer values of the kind, or None for floating point.
    """

    name: str
    bits: int
    ctype: str
    range: Interval | None

    @property
    def is_float(self) -> bool:
        """Whether the kind is floating point."""
        return self.range is None

    @property
    def is_signed(self) -> bool:
        """Whether the kind is a signed integer (floats are neither)."""
        return self.range is not None and self.range.lo < 0

    @property
    def rank_at_least_int(self) -> bool:
        """Whether C integer promotion leaves the kind unchanged."""
        return not self.is_float and self.bits >= 32

    def holds(self, value: int) -> bool:
        """Whether ``value`` is a value of the integer kind."""
        return Interval(value, value).fits(self)


def _int(bits: int) -> FixedWidth:
    top = 2 ** (bits - 1)
    return FixedWidth(
        f"int{bits}", bits, f"int{bits}_t", Interval(-top, top - 1)
    )


def _uint(bits: int) -> FixedWidth:
    return FixedWidth(
        f"uint{bits}", bits, f"uint{bits}_t", Interval(0, 2**bits - 1)
    )


_ENTRIES: Final = (
    *(_int(n) for n in (8, 16, 32, 64)),
    *(_uint(n) for n in (8, 16, 32, 64)),
    FixedWidth("float32", 32, "float", None),
    FixedWidth("float64", 64, "double", None),
)

#: The kinds by library feature name (``FIXED_WIDTH["uint8"]``).
FIXED_WIDTH: Final[Mapping[str, FixedWidth]] = {
    entry.name: entry for entry in _ENTRIES
}


def fixed_width_of(feature: syside.Feature) -> FixedWidth | None:
    """Find the fixed-width kind of a feature.

    The kind is that of the library feature ``feature`` specializes
    (transitively, through subsettings and redefinitions); a library
    feature is its own kind.

    Args:
        feature: The feature to classify.

    Returns:
        The kind, or None if the feature specializes no library feature.

    Raises:
        UnsupportedConstructError: If it specializes several kinds, for
            example by redefining a ``uint8`` attribute as ``:> uint16``.
    """
    hits = [
        kind for kind in _ENTRIES if feature.specializes((_LIBRARY, kind.name))
    ]
    if len(hits) > 1:
        names = ", ".join(sorted(kind.name for kind in hits))
        raise UnsupportedConstructError(
            f"{feature.qualified_name} specializes several fixed-width "
            f"kinds ({names})"
        )
    return hits[0] if hits else None
