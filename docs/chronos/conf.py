project = "chronos-logger"
extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx_immaterial"]
html_theme = "sphinx_immaterial"
html_theme_options = {
    "palette": [
        {"media": "(prefers-color-scheme: light)", "scheme": "default"},
        {"media": "(prefers-color-scheme: dark)", "scheme": "slate"},
    ],
}
# MyST pages are .md; index.md is the entry point (Sphinx root_doc).
root_doc = "index"
