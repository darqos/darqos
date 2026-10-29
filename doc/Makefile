# Minimal makefile for Sphinx documentation
#

# You can set these variables from the command line, and also
# from the environment for the first two.  Warnings are errors, as in CI.
# Sphinx is the development virtual environment's, if there is one (it
# has the theme), or else whichever is on the path, as in CI.
SPHINXOPTS    ?= -W --keep-going
SPHINXBUILD   ?= $(or $(wildcard $(abspath ../venv313/bin/sphinx-build)),sphinx-build)
SOURCEDIR     = .
BUILDDIR      = _build

# Put it first so that "make" without argument is like "make help".
help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

.PHONY: help Makefile

# Catch-all target: route all unknown targets to Sphinx using the new
# "make mode" option.  $(O) is meant as a shortcut for $(SPHINXOPTS).
%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
