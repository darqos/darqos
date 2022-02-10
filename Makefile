all: world

world clean:
	(cd services/storage; make $@)
	(cd services/history; make $@)

