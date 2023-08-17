# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# Project information
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

from datetime import date
from importlib.metadata import metadata


# Import project metadata from the setuptools package

project = metadata("django-sqlite")["name"]

summary = metadata("django-sqlite")["summary"]

author = "Afonso Silva"

copyright = f"{date.today().year}, {author}"

version = metadata("django-sqlite")["version"]

release = version


# General configuration
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "myst_parser",
]

templates_path = ["_templates"]

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]


# HTML output
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "alabaster"

html_theme_options = {
    "description": summary,
    "github_user": "ajcerejeira",
    "github_repo": "django-sqlite",
}


# Intersphinx
# https://www.sphinx-doc.org/en/master/usage/extensions/intersphinx.html

intersphinx_mapping = {
    "python": (
        "https://docs.python.org/3",
        None,
    ),
    "django": (
        "https://docs.djangoproject.com/en/stable/",
        "https://docs.djangoproject.com/en/stable/_objects/",
    ),
}
