#! /bin/bash
########################################################################
# darqos
# Copyright (C) 2020-2022 David Arnold

export PYTHONPATH=/Users/d/work/personal/darqos

# Start services.
services/storage/dist/storaged &
services/history/dist/historyd &

#python services/metadata/main.py &
#python services/index/main.py &

# Start GUI shell.
#python services/terminal/main.py &

python darq/type/text.py
