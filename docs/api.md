# API Reference

The rosetta backend: the Lingua Franca program builder, its intermediate
representation, the Python expression emitter, serialization, and part
composition. The shared front-end and plugin contract are documented in the
core package.

<!-- The autodoc directives below are wrapped in `{eval-rst}` fences on
purpose: a bare `{automodule}` MyST fence renders autodoc's generated
reStructuredText as literal text, so the RST escape hatch is required for the
API reference to render. -->

## Backend entry point

```{eval-rst}
.. automodule:: sysmlc_rosetta.backend
```

## Program construction

```{eval-rst}
.. automodule:: sysmlc_rosetta.builder
.. automodule:: sysmlc_rosetta.program
.. automodule:: sysmlc_rosetta.codegen
```

## Serialization

```{eval-rst}
.. automodule:: sysmlc_rosetta.serialize
```

## Part systems

```{eval-rst}
.. automodule:: sysmlc_rosetta.parts
```
