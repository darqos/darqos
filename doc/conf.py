# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'The Darq Book'
copyright = '2023-2026, David Arnold'
author = 'David Arnold'
release = 'First Edition'


templates_path = ['_templates']


# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = []

exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

#html_theme = 'alabaster'
#html_theme = 'furo'
#html_theme = 'sphinx_library'
html_theme = 'sphinx_book_theme'

html_title = "The Darq Book"

html_sidebars = {
    "**": [
        "navbar-logo.html",
        "sidebar-subtitle.html",
        "search-button-field.html",
        "sbt-sidebar-nav.html",
    ],
}


# -- Options for LaTeX (PDF) output ------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-latex-output

latex_documents = [
    ('index', 'darqos.tex', project, author, 'manual'),
]

latex_elements = {
    'releasename': 'First Edition',

    # Avoid "Too deeply nested" errors from LaTeX
    'preamble': r'''
\usepackage{enumitem}
\setlistdepth{20}
\renewlist{itemize}{itemize}{20}
\renewlist{enumerate}{enumerate}{20}
\setlist[itemize]{label=$\cdot$}
\setlist[itemize,1]{label=\textbullet}
\setlist[itemize,2]{label=\textbullet}
\setlist[itemize,3]{label=\textbullet}
\setlist[itemize,4]{label=\textbullet}
\setlist[itemize,5]{label=\textbullet}
\setlist[itemize,6]{label=\textbullet}
''',
}
