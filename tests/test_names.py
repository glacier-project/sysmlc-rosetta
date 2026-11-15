from __future__ import annotations

import pytest
from sysmlc.errors import UnsupportedConstructError

from sysmlc_rosetta.names import ScopedNames


def test_identity_and_scope_keep_names_stable() -> None:
    names = ScopedNames()
    assert names.assign("A", "declaration", "first", "S1.1") == "S1_1"
    assert names.assign("A", "declaration", "first", "ignored") == "S1_1"
    assert names.assign("B", "declaration", "first", "S1.1") == "S1_1"
    assert names.assign("A", "declaration", "second", "S1_1") == "S1_1_01"


def test_fixed_reservations_and_case_insensitive_reactor_classes() -> None:
    names = ScopedNames()
    names.reserve("", "reactor", "Machine")
    assert names.assign("", "reactor", "other", "machine") == "machine_01"
    names.reserve("A", "declaration", "done")
    assert names.assign("A", "declaration", "state", "done") == "done_01"


@pytest.mark.parametrize(
    ("preferred", "expected"),
    [
        ("lf", "lf"),
        ("lf_ready", "state_lf_ready"),
        ("sysmlc_ready", "state_sysmlc_ready"),
        ("class", "class_01"),
        ("NONE", "NONE_01"),
        ("True", "True_01"),
        (".", "__01"),
        ("1.ready", "state_1_ready"),
    ],
)
def test_portable_names_escape_reserved_words_and_prefixes(
    preferred: str, expected: str
) -> None:
    assert (
        ScopedNames().assign("A", "declaration", "state", preferred) == expected
    )


def test_collision_of_safe_name_with_reserved_suffix_prefix_terminates() -> (
    None
):
    names = ScopedNames()
    assert names.assign("A", "declaration", "first", "lf") == "lf"
    assert names.assign("A", "declaration", "second", "lf") == "state_lf_01"


def test_late_external_reservations_cannot_overwrite_assigned_symbols() -> None:
    names = ScopedNames()
    names.assign("A", "declaration", "first", "idle")
    with pytest.raises(UnsupportedConstructError, match="reserved after"):
        names.reserve("A", "declaration", "fresh", "idle")
    assert names.assign("A", "declaration", "second", "fresh") == "fresh"
