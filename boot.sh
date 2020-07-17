#! /bin/bash

export PYTHONPATH=/Users/d/work/personal/darqos

# Start services.
python services/storage/main.py &
python services/history/main.py &

python services/terminal/main.py &


