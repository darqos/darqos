# darqos
# Copyright (C) 2022 David Arnold

all: world

world clean:
	# Services
	(cd services/history; make $@)
	(cd services/metadata; make $@)
	(cd services/storage; make $@)

	# Types
	(cd types/text; make $@)
