from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sysmlc.errors import UnsupportedConstructError
from sysmlc.sysml.loading import load_model

from sysmlc_rosetta.builder import build_program
from sysmlc_rosetta.serialize import to_lf
from sysmlc_rosetta.targets.c import CTarget
from sysmlc_rosetta.targets.python import PythonTarget
from sysmlc_rosetta.targets.uc import UcTarget
from tests.conftest import FIXTURES_DIR
from tests.test_nested_data import _execute, _values

if TYPE_CHECKING:
    from pathlib import Path

    from sysmlc_rosetta.targets.base import LfTarget

FIXTURE = FIXTURES_DIR / "parallel-data"


@pytest.mark.lf
@pytest.mark.parametrize("target_type", [PythonTarget, CTarget])
def test_parent_cleanup_shares_root_storage(
    tmp_path: Path, target_type: type[LfTarget]
) -> None:
    target = target_type()
    program = build_program(
        load_model(FIXTURE), "ParallelData::Cleanup", target=target
    )
    result = _execute(tmp_path, program, target)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _values(result.stdout, "sequence") == [12345]


def test_parent_cleanup_generates_uc() -> None:
    program = build_program(
        load_model(FIXTURE), "ParallelData::Cleanup", target=UcTarget()
    )
    assert "_data" in to_lf(program)


@pytest.mark.parametrize(
    "regional_work",
    [
        "state active { exit assign sequence := sequence + 1; } "
        "state other; transition first active accept after 0.001 [s] then other;",
        "state active { exit assign sequence := sequence + 1; } "
        "transition first active accept after 0.001 [s] then done;",
        "state active; state other; transition first active "
        "accept after 0.001 [s] if sequence > 0 then other;",
        "state active; state other; transition first active "
        "accept after 0.001 [s] "
        "do assign sequence := sequence + 1 then other;",
        "exit assign sequence := sequence + 1; state active; "
        "transition first active accept after 0.001 [s] then done;",
        "exit assign sequence := sequence + 1; state active; "
        "transition first active then done;",
        "state active { entry assign sequence := sequence + 1; }",
    ],
)
def test_independent_regional_access_is_diagnosed(
    tmp_path: Path, regional_work: str
) -> None:
    (tmp_path / "model.sysml").write_text(f"""
        package ParallelData {{
            private import ScalarValues::*;
            private import SI::*;
            state def Machine {{
                attribute sequence : Integer := 0;
                entry; then working;
                state working parallel {{
                    state region {{
                        entry; then active;
                        {regional_work}
                    }}
                }}
                state finishedState;
                transition first working accept after 0.002 [s]
                    then finishedState;
            }}
        }}
    """)
    with pytest.raises(
        UnsupportedConstructError, match="requires ordered parent cleanup"
    ):
        build_program(load_model(tmp_path), "ParallelData::Machine")


@pytest.mark.parametrize("ordinary_departure", [False, True])
def test_cleanup_checks_every_enclosing_parallel_boundary(
    tmp_path: Path, ordinary_departure: bool
) -> None:
    departure = (
        "state other; transition first inner accept after 0.001 [s] then other;"
        if ordinary_departure
        else ""
    )
    (tmp_path / "model.sysml").write_text(f"""
        package ParallelData {{
            private import ScalarValues::*;
            private import SI::*;
            state def Machine {{
                attribute sequence : Integer := 0;
                entry; then outer;
                state outer parallel {{
                    state region {{
                        entry; then inner;
                        state inner parallel {{
                            state innerRegion {{
                                entry; then active;
                                state active {{
                                    exit assign sequence := sequence + 1;
                                }}
                            }}
                        }}
                        {departure}
                    }}
                }}
                state finishedState;
                transition first outer accept after 0.002 [s]
                    then finishedState;
            }}
        }}
    """)
    if ordinary_departure:
        with pytest.raises(
            UnsupportedConstructError, match="requires ordered parent cleanup"
        ):
            build_program(load_model(tmp_path), "ParallelData::Machine")
    else:
        build_program(load_model(tmp_path), "ParallelData::Machine")


@pytest.mark.parametrize(
    "parallel_body",
    [
        """
        state region {
            entry; then nested;
            state nested {
                entry; then active;
                exit assign sequence := sequence + 1;
                state active;
                transition first active accept after 0.001 [s] then other;
            }
            state other;
        }
        """,
        """
        state firstRegion {
            entry; then active;
            state active { exit assign sequence := sequence + 1; }
        }
        state secondRegion {
            entry; then active;
            state active;
            transition first active accept after 0.001 [s] then finishedState;
        }
        """,
    ],
)
def test_regional_escape_cannot_admit_shared_exit(
    tmp_path: Path, parallel_body: str
) -> None:
    (tmp_path / "model.sysml").write_text(f"""
        package ParallelData {{
            private import ScalarValues::*;
            private import SI::*;
            state def Machine {{
                attribute sequence : Integer := 0;
                entry; then working;
                state working parallel {{ {parallel_body} }}
                state finishedState;
                transition first working accept after 0.002 [s]
                    then finishedState;
            }}
        }}
    """)
    with pytest.raises(
        UnsupportedConstructError, match="requires ordered parent cleanup"
    ):
        build_program(load_model(tmp_path), "ParallelData::Machine")
