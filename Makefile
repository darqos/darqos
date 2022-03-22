all: world

world clean:
	# Services
	(cd services/storage; make $@)
	(cd services/history; make $@)

	# Types
	(cd types/text; make $@)
