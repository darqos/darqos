# darqos
# Copyright (C) 2022 David Arnold

all: world

world clean:
	# Services
	(cd services/history; make $@)
	(cd services/index; make $@)
	(cd services/metadata; make $@)
	(cd services/security; make $@)
	(cd services/storage; make $@)

	# Types
	(cd types/text; make $@)
