# darqos
# Copyright (C) 2022 David Arnold

world: historyd

historyd: main.py main.spec
	pyinstaller -F main.spec

clean:
	rm -rf build dist __pycache__
