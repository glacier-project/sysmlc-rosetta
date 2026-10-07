# 🪨 sysmlc-rosetta

The **rosetta** backend for [sysmlc](https://github.com/glacier-project/sysmlc-core):
it translates SysML v2 state machines into [Lingua Franca](https://www.lf-lang.org/)
modal-reactor programs targeting Python.

**Status:** in progress.

## Overview

Rosetta maps a SysML state machine onto a Lingua Franca modal reactor: states
become modes and transitions become reactions. A connected part system becomes
a `main reactor` that instantiates one reactor per part and wires its connected
ports, leaving orchestration — routing, scheduling and logical time — to the
Lingua Franca runtime.

[`docs/rosetta-mapping.md`](docs/rosetta-mapping.md) is the canonical,
construct-by-construct record of the SysML-to-Lingua-Franca mapping and of the
boundaries of what this backend supports.

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

Installed beside the core, the backend registers itself on the shared CLI.
Model arguments take either a path or a bundled corpus name. When a model has
exactly one top-level part usage, `--element` can be omitted and the CLI
selects the system target:

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

Two flags shape the generated program's `target` header, and apply to
top-level part usages only:

| Flag        | Meaning                                               |
| ----------- | ----------------------------------------------------- |
| `--timeout` | run timeout for the generated program, e.g. `"5 sec"` |
| `--fast`    | enable fast mode in the generated program             |

External `calc def` calls can be backed by a Python file passed with
`--python`, matched to SysML functions by simple name.

`examples/run_all_rosetta.py` builds, compiles and runs every showcase model
end to end and reports a per-model verdict.

## Layout

```
sysmlc_rosetta/
├── backend.py       # RosettaBackend: the sysmlc plugin entry point
├── builder.py       # neutral facts -> modal reactor
├── codegen.py       # expressions -> Python source for reactions
├── parts.py         # connected part systems -> main reactor
├── program.py       # the LfProgram model
└── serialize.py     # Lingua Franca source output
```

## Development

Bundled model behavior is owned by [sysmlc-models](https://github.com/glacier-project/sysmlc-models). The scenario wrapper in this repository runs those shared contracts with this backend.

Local tests verify LF reactions, reactor wiring and runtime integration, using small fixtures for target-specific behavior. Shared parsing and neutral semantic checks belong in [sysmlc-core](https://github.com/glacier-project/sysmlc-core).

```bash
uv run tox                       # tests, type checking, formatting, coverage, docs
uv run pytest tests              # tests only (excludes the lf-marked tests)
uv run tox run -e lf             # compile and run generated LF programs
uv run pyrefly check             # type checking
uv run tox run -e formatter      # ruff check --fix and ruff format
uv run tox run -e docs           # sphinx -W
uv run pre-commit install        # once after cloning
```

The inner-first and run-to-completion tests compare rosetta's output against
[quake](https://github.com/glacier-project/sysmlc-quake)'s statecharts, which
is why quake is a test dependency of this repository.

## Citation

If you use rosetta in your research, please cite:

> S. Gaiardelli, M. Libro, P. Turco, E. Fraccaroli, M. Lora, S. Chakraborty and
> F. Fummi, "Rosetta: Compiling SysML v2 Behavior into Lingua Franca Modal
> Reactors," *IEEE Embedded Systems Letters*, 2026,
> doi: [10.1109/LES.2026.3730614](https://doi.org/10.1109/LES.2026.3730614).

```bibtex
@article{gaiardelli2026rosetta,
  author  = {Gaiardelli, Sebastiano and Libro, Mario and Turco, Pietro and
             Fraccaroli, Enrico and Lora, Michele and Chakraborty, Samarjit and
             Fummi, Franco},
  title   = {Rosetta: Compiling {SysML} v2 Behavior into {Lingua Franca} Modal
             Reactors},
  journal = {IEEE Embedded Systems Letters},
  year    = {2026},
  doi     = {10.1109/LES.2026.3730614},
}
```
