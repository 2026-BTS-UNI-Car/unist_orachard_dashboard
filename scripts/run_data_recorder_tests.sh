#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/iron/setup.bash
set -u
cd /app
python3 -m unittest tests.test_data_recorder_service -v
