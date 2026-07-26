# sysmlc-rosetta

The **rosetta** backend for [sysmlc](https://github.com/glacier-project/sysmlc-core):
it translates SysML v2 state machines into [Lingua Franca](https://www.lf-lang.org/)
modal-reactor programs targeting Python.

`docs/rosetta-mapping.md` is the canonical, construct-by-construct record of
the SysML-to-Lingua-Franca mapping and of the boundaries of what this backend
supports.

## Prerequisites

The core parses SysML v2 with
[Syside Automator](https://docs.sensmetry.com/automator/index.html), which
requires a license key. Create a `.env` file in the project root:

```
SYSIDE_LICENSE_KEY=your-key-here
```

Compiling and running the generated programs additionally needs `lfc` and
Java; without them the `lf`-marked tests are skipped.

## Installation

```bash
uv sync --extra dev
```

This installs the core (`sysmlc`) and the model corpora
([sysmlc-models](https://github.com/glacier-project/sysmlc-models)) that the
tests and examples use.

## Usage

Installing this package beside the core registers the backend through the
`sysmlc.backends` entry-point group, which makes it available on the shared
CLI. Model arguments accept either a path to a model directory or the name of
a bundled corpus model. When a model has exactly one top-level part usage,
`--element` can be omitted and the CLI selects the system target:

```bash
uv run sysmlc rosetta build showcase/milling-workcell -o out/
```

To translate a bare state machine, select the state definition directly:

```bash
uv run sysmlc rosetta build showcase/milling-workcell \
  -e MillingWorkcell::MillingWorkcellBehavior -o out/
```

Attribute initial values can be overridden at build time from a hierarchical
YAML file whose nesting mirrors qualified names:

```bash
uv run sysmlc rosetta build showcase/thermostat \
  -e Thermostat::ThermostatBehavior \
  --values values.yaml -o out/
```

`examples/run_all_rosetta.py` builds, compiles and runs every showcase model
end to end and reports a per-model verdict.

## Development

```bash
uv run tox                       # tests, type checking, formatting, coverage, docs
uv run pytest tests              # tests only (excludes the lf-marked tests)
uv run tox run -e lf             # compile and run generated LF programs
uv run pyrefly check             # type checking
uv run tox run -e formatter      # ruff check --fix and ruff format
uv run tox run -e docs           # sphinx -W
uv run pre-commit install        # once after cloning
```
