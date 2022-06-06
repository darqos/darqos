#! /bin/bash
########################################################################
# darqos
# Copyright (C) 2020-2022 David Arnold

export PYTHONPATH=/Users/d/work/personal/darqos

# Start services.
services/history/dist/history-service &
#services/index/dist/index-service &
#services/index/dist/metadata-service &
#services/index/dist/security-service &
services/storage/dist/storage-service &
#services/storage/dist/terminal-service &

# Start GUI shell.
python services/terminal/main.py &
