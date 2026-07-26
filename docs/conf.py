"""Sphinx configuration for the sysmlc-rosetta documentation."""

from __future__ import annotations

import sys
from pathlib import Path

from pygments.lexers.special import TextLexer
from sphinx.highlighting import lexers

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The mapping documentation fences its model listings as ``sysml`` and its
# generated programs as ``lf``; Pygments ships neither lexer, so render
# those blocks unhighlighted rather than failing the strict build on an
# unknown lexer name.
lexers["sysml"] = TextLexer()
lexers["lf"] = TextLexer()

project = "sysmlc-rosetta"
author = "Mario Libro"
copyright = "2026, Mario Libro"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
]

source_suffix = {
    ".md": "markdown",
    ".rst": "restructuredtext",
}

exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
]

html_theme = "furo"
html_title = "sysmlc-rosetta documentation"
suppress_warnings = ["sphinx_autodoc_typehints.forward_reference"]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
]

autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
}
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"
autodoc_typehints_format = "short"

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_use_param = True
napoleon_use_rtype = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
