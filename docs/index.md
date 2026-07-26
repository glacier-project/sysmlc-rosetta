# sysmlc-rosetta

`sysmlc-rosetta` is the **rosetta** backend for
[sysmlc](https://github.com/glacier-project/sysmlc-core): it translates SysML v2
state machines into [Lingua Franca](https://www.lf-lang.org/) modal-reactor
programs targeting Python.

The backend registers through the `sysmlc.backends` entry-point group, so
installing this package beside the core makes `sysmlc rosetta build` available.

```{toctree}
:maxdepth: 2
:caption: Contents

rosetta-mapping
api
```

## Development

Install the documentation dependencies and run the live documentation server:

```bash
uv sync --extra dev --extra docs
uv run sphinx-autobuild docs docs/_build/html
```

Build the static site with:

```bash
uv run tox -e docs
```

## Construct mapping

`rosetta-mapping` is the canonical, construct-by-construct record of how each
SysML v2 state-machine construct becomes Lingua Franca, including the
boundaries of what the backend supports.
