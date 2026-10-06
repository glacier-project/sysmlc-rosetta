from __future__ import annotations

from pathlib import Path

from sysmlc.cli import main
from sysmlc_models.sm_examples import SM_EXAMPLES_DIR

RIG_DIR = Path(__file__).resolve().parent / "fixtures" / "rig-pair"


def test_rig_auto_select_builds_composition(tmp_path: Path) -> None:
    exit_code = main(["rosetta", "build", str(RIG_DIR), "-o", str(tmp_path)])
    assert exit_code == 0
    text = (tmp_path / "PlantRig.lf").read_text()
    assert "reactor PlantRig {" in text
    assert "plant = new Plant()" in text


def test_explicit_rig_element_builds_composition(tmp_path: Path) -> None:
    exit_code = main(
        [
            "rosetta",
            "build",
            str(RIG_DIR),
            "-e",
            "RigPair::PlantRig",
            "-o",
            str(tmp_path),
        ]
    )
    assert exit_code == 0
    text = (tmp_path / "PlantRig.lf").read_text()
    assert "reactor PlantRig {" in text


def test_explicit_state_def_still_builds_bare_machine(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "rosetta",
            "build",
            str(RIG_DIR),
            "-e",
            "RigPair::Plant",
            "-o",
            str(tmp_path),
        ]
    )
    assert exit_code == 0
    text = (tmp_path / "Plant.lf").read_text()
    assert "reactor PlantRig" not in text
    # assert "Done_act" in text  # bare build: the send stays a self-event


def test_rig_values_apply_per_machine(tmp_path: Path) -> None:
    values = tmp_path / "values.yaml"
    values.write_text("RigPair:\n  PlantTest:\n    verdict: 1\n")
    exit_code = main(
        [
            "rosetta",
            "build",
            str(RIG_DIR),
            "-o",
            str(tmp_path / "out"),
            "--values",
            str(values),
        ]
    )
    assert exit_code == 0
    text = (tmp_path / "out" / "PlantRig.lf").read_text()
    assert "state verdict = {= 1 =}" in text


def test_build_with_python_copies_module_and_imports(tmp_path: Path) -> None:
    from sysmlc.cli import main

    model = tmp_path / "model"
    model.mkdir()
    (model / "m.sysml").write_text(
        "package M {\n"
        "  private import ScalarValues::*;\n"
        "  private import SI::*;\n"
        "  package P { calc def step { in x : Real; return : Real; } }\n"
        "  state def S {\n"
        "    attribute x : Real := 0.0;\n"
        "    entry; then a; state a; state b;\n"
        "    transition first a accept after 0.1 [s]\n"
        "      do assign x := P::step(x) then b;\n"
        "  }\n"
        "}\n"
    )
    py = tmp_path / "ext.py"
    py.write_text("def step(x):\n    return x + 1.0\n")
    out = tmp_path / "out"
    rc = main(
        [
            "rosetta",
            "build",
            str(model),
            "-e",
            "M::S",
            "-o",
            str(out),
            "--python",
            str(py),
        ]
    )
    assert rc == 0
    lf = (out / "S.lf").read_text()
    assert "from ext import step" in lf
    assert "self.x = step(self.x)" in lf
    assert (out / "ext.py").exists()  # copied next to the .lf


def test_build_reps_generates_module_beside_lf(tmp_path: Path) -> None:
    # sm15-rep carries the calc body as a Python rep.
    out = tmp_path / "out"
    rc = main(
        [
            "rosetta",
            "build",
            str(SM_EXAMPLES_DIR / "sm15-rep"),
            "-e",
            "SM15Rep::Ramp",
            "-o",
            str(out),
        ]
    )
    assert rc == 0
    lf = (out / "Ramp.lf").read_text()
    assert "from Ramp_impl import step" in lf
    assert '"Ramp_impl.py"' in lf
    assert "step(self.x, 0.1)" in lf
    assert "def step(x, dt):" in (out / "Ramp_impl.py").read_text()


def test_rep_build_accepts_generated_module_as_explicit_override(
    tmp_path: Path,
) -> None:
    # A previously generated implementation replaces model representations.
    rep_out = tmp_path / "rep"
    rc = main(
        [
            "rosetta",
            "build",
            str(SM_EXAMPLES_DIR / "sm15-rep"),
            "-e",
            "SM15Rep::Ramp",
            "-o",
            str(rep_out),
        ]
    )
    assert rc == 0
    python_out = tmp_path / "python"
    rc = main(
        [
            "rosetta",
            "build",
            str(SM_EXAMPLES_DIR / "sm15-rep"),
            "-e",
            "SM15Rep::Ramp",
            "-o",
            str(python_out),
            "--python",
            str(rep_out / "Ramp_impl.py"),
        ]
    )
    assert rc == 0
    assert (python_out / "Ramp_impl.py").read_text() == (
        rep_out / "Ramp_impl.py"
    ).read_text()


def test_explicit_python_replaces_model_reps(tmp_path: Path) -> None:
    # Explicit input replaces representation resolution in its language.
    py = tmp_path / "ramp.py"
    py.write_text("def step(x, dt):\n    return x + dt\n")
    out = tmp_path / "out"
    rc = main(
        [
            "rosetta",
            "build",
            str(SM_EXAMPLES_DIR / "sm15-rep"),
            "-e",
            "SM15Rep::Ramp",
            "-o",
            str(out),
            "--python",
            str(py),
        ]
    )
    assert rc == 0
    lf = (out / "Ramp.lf").read_text()
    assert "from ramp import step" in lf
    assert '"ramp.py"' in lf
    assert '"Ramp_impl.py"' not in lf
    assert not (out / "Ramp_impl.py").exists()


def test_build_part_system_emits_main_reactor(tmp_path: Path) -> None:
    out = tmp_path / "out"
    rc = main(
        [
            "rosetta",
            "build",
            str(SM_EXAMPLES_DIR / "part01-two-parts"),
            "-e",
            "Part01::pingSystem",
            "-o",
            str(out),
            "--timeout",
            "5 sec",
            "--fast",
        ]
    )
    assert rc == 0
    lf = (out / "pingSystem.lf").read_text()
    assert "main reactor {" in lf
    assert "fast: true" in lf
    assert "timeout: 5 sec" in lf
    assert "plant = new Plant()" in lf


def test_single_part_system_auto_selected(tmp_path: Path) -> None:
    # No --element: the model's single top-level part usage is selected.
    out = tmp_path / "out"
    rc = main(
        [
            "rosetta",
            "build",
            str(SM_EXAMPLES_DIR / "part01-two-parts"),
            "-o",
            str(out),
        ]
    )
    assert rc == 0
    assert (out / "pingSystem.lf").exists()


def test_build_part_system_with_python_copies_module_and_imports(
    tmp_path: Path,
) -> None:
    # --python must work for part usages: the generated .lf includes the
    # external import and the bump.py module is copied beside the .lf.
    part_ext = SM_EXAMPLES_DIR / "part-external"
    out = tmp_path / "out"
    rc = main(
        [
            "rosetta",
            "build",
            str(part_ext),
            "-e",
            "PartExt::counterSystem",
            "-o",
            str(out),
            "--python",
            str(part_ext / "bump.py"),
        ]
    )
    assert rc == 0
    lf = (out / "counterSystem.lf").read_text()
    assert "from bump import bump" in lf
    assert '"bump.py"' in lf  # listed in files: so lfc copies it to src-gen
    assert (out / "bump.py").exists()  # copied beside the .lf


def test_rosetta_run_is_rejected() -> None:
    rc = main(["rosetta", "run", str(SM_EXAMPLES_DIR / "part01-two-parts")])

    assert rc == 1
