#! /bin/bash

# Start services.
python services/storage.py &
python services/ui/ui.py &


