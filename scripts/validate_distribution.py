#!/usr/bin/env python3
"""Build the project distribution and validate the built wheel.

Validation covers two properties: the package and its backend module
import cleanly away from the source checkout, and the wheel registers the
backend in the ``sysmlc.backends`` entry-point group the shared CLI
discovers.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

DEFAULT_PACKAGE = "sysmlc_rosetta"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--package",
        default=DEFAULT_PACKAGE,
        help="Import package name to verify from the built wheel.",
    )
    parser.add_argument(
        "--dist-dir",
        default="dist",
        help="Directory where build artifacts should be written.",
    )
    return parser.parse_args()


def run(command: list[str], *, cwd: Path | None = None) -> None:
    """Run a command and fail immediately on errors."""
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def build_distribution(dist_dir: Path) -> Path:
    """Build sdist and wheel artifacts and return the wheel path."""
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True)

    run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--sdist",
            "--wheel",
            "--outdir",
            str(dist_dir),
        ]
    )

    wheels = sorted(dist_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            f"Expected exactly one wheel in {dist_dir}, found {len(wheels)}."
        )
    if not any(dist_dir.glob("*.tar.gz")):
        raise RuntimeError(f"Expected an sdist artifact in {dist_dir}.")
    return wheels[0]


def verify_wheel_import(wheel: Path, package: str) -> None:
    """Import the built wheel away from the source checkout.

    Imports the backend module rather than only the top-level package, so
    a distribution whose backend fails to import cannot pass.

    Args:
        wheel: The built wheel archive.
        package: Import package name to load from the wheel.
    """
    wheel_path = wheel.resolve()
    import_code = (
        "import importlib; "
        "import sys; "
        f"sys.path.insert(0, {str(wheel_path)!r}); "
        f"importlib.import_module({package!r}); "
        f"importlib.import_module({package + '.backend'!r})"
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        run([sys.executable, "-c", import_code], cwd=Path(tmp_dir))


def verify_backend_entry_point(wheel: Path, package: str) -> None:
    """Check the wheel registers the backend for the shared CLI.

    The backend reaches users only through the ``sysmlc.backends``
    entry-point group; a wheel that ships the code but loses the
    registration installs cleanly and then does nothing.

    Args:
        wheel: The built wheel archive.
        package: Import package name expected to provide the backend.

    Raises:
        RuntimeError: If the wheel declares no entry points, or none in
            the ``sysmlc.backends`` group pointing at ``package``.
    """
    with zipfile.ZipFile(wheel) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/entry_points.txt")
        ]
        if not names:
            raise RuntimeError(f"{wheel.name} declares no entry points.")
        declared = archive.read(names[0]).decode()
    if "[sysmlc.backends]" not in declared:
        raise RuntimeError(
            f"{wheel.name} declares no 'sysmlc.backends' entry-point group:"
            f"\n{declared}"
        )
    if f"{package}.backend:" not in declared:
        raise RuntimeError(
            f"{wheel.name} registers no backend from {package!r}:\n{declared}"
        )
    print(f"wheel registers a sysmlc.backends entry point from {package}")


def main() -> int:
    """Build and validate the project distribution."""
    args = parse_args()
    wheel = build_distribution(Path(args.dist_dir))
    verify_wheel_import(wheel, args.package)
    verify_backend_entry_point(wheel, args.package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
