from __future__ import annotations

from typing import TYPE_CHECKING

from examples import run_all_rosetta

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _configure_showcase(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    showcase = root / "showcase"
    monkeypatch.setattr(run_all_rosetta, "SHOWCASE_DIR", showcase)
    monkeypatch.setattr(run_all_rosetta, "SHARED_PYTHON_SUPPORT", {})
    return showcase


def test_model_dirs_accepts_nested_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    showcase = _configure_showcase(monkeypatch, tmp_path)
    nested = showcase / "furuta-pendulum" / "deterministic"
    nested.mkdir(parents=True)
    (nested / "furuta.sysml").touch()

    assert run_all_rosetta._model_name(nested) == (
        "furuta-pendulum/deterministic"
    )
    assert run_all_rosetta._model_dirs(["furuta-pendulum/deterministic"]) == [
        nested
    ]


def test_python_arguments_selects_only_configured_shared_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    showcase = _configure_showcase(monkeypatch, tmp_path)
    deterministic = showcase / "furuta-pendulum" / "deterministic"
    reference = showcase / "furuta-pendulum" / "nondeterministic"
    deterministic.mkdir(parents=True)
    reference.mkdir()
    support = deterministic.parent / "furuta_physics.py"
    support.touch()
    monkeypatch.setattr(
        run_all_rosetta,
        "SHARED_PYTHON_SUPPORT",
        {"furuta-pendulum/deterministic": support},
    )

    assert run_all_rosetta._python_arguments(deterministic) == [
        "--python",
        str(support),
    ]
    assert run_all_rosetta._python_arguments(reference) == []
