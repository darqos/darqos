# darqos
# Copyright (C) 2022 David Arnold

world: terminal-service

terminal-service: main.py main.spec
	env PYTHONPATH=../.. pyinstaller -F main.spec

clean:
	rm -rf build dist __pycache__
