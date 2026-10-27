# darqos
# Copyright (C) 2022-2026 David Arnold

all: world

# Python modules generated from the DIDL protocol sources.  Both are
# committed: rerun this after changing a .didl file.  The IR is only an
# intermediate.
DIDL := $(wildcard lib/didl/*.didl)
PROTOCOLS := $(patsubst lib/didl/%.didl,darq/protocols/%.py,$(DIDL))

protocols: $(PROTOCOLS)

darq/protocols/%.py: lib/didl/%.didl bin/didlc bin/didlpygen
	bin/didlc -o darq/protocols/$*.dir $<
	bin/didlpygen -o $@ darq/protocols/$*.dir
	rm -f darq/protocols/$*.dir

# HTML and PDF documentation, in doc/_build/html and doc/_build/latex.
docs:
	$(MAKE) -C doc html latexpdf SPHINXBUILD=$(abspath venv313/bin/sphinx-build)

world: docs protocols

.PHONY: all world protocols docs
