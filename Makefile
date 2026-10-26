# darqos
# Copyright (C) 2022 David Arnold

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

.PHONY: all world clean protocols

world clean:
	# Services
	(cd services/history; make $@)
	(cd services/index; make $@)
	(cd services/metadata; make $@)
	(cd services/security; make $@)
	(cd services/terminal; make $@)

	# Types
	(cd types/text; make $@)
