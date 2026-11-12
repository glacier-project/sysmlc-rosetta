# API Reference

The rosetta backend: the Lingua Franca program builder, its intermediate
representation, the target languages and their expression emitters,
serialization, and part composition. The shared front-end and plugin contract are documented in the
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
.. automodule:: sysmlc_rosetta.lifecycle
.. automodule:: sysmlc_rosetta.program
.. automodule:: sysmlc_rosetta.statements
.. automodule:: sysmlc_rosetta.codegen
```

## Target languages

```{eval-rst}
.. automodule:: sysmlc_rosetta.targets.base
.. automodule:: sysmlc_rosetta.targets.python
.. automodule:: sysmlc_rosetta.targets.c
.. automodule:: sysmlc_rosetta.targets.uc
.. automodule:: sysmlc_rosetta.c_codegen
.. automodule:: sysmlc_rosetta.c_types
```

## Serialization

```{eval-rst}
.. automodule:: sysmlc_rosetta.serialize
```

## Part systems

```{eval-rst}
.. automodule:: sysmlc_rosetta.parts
```
