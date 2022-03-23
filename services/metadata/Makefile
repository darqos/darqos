# darqos
# Copyright (C) 2022 David Arnold

world: metadata-service

metadata-service: main.py main.spec
	env PYTHONPATH=../.. pyinstaller -F main.spec

clean:
	rm -rf build dist __pycache__
