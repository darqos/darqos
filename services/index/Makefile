# darqos
# Copyright (C) 2022 David Arnold

world: index-service

index-service: main.py main.spec
	env PYTHONPATH=../.. pyinstaller -F main.spec

clean:
	rm -rf build dist __pycache__
