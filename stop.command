#!/bin/bash
# Double-click to stop ATLAS (stops whatever is serving the dashboard port).
PIDS=$(lsof -ti tcp:8530 -sTCP:LISTEN)
if [ -n "$PIDS" ]; then kill $PIDS && echo "ATLAS stopped."; else echo "ATLAS was not running."; fi
sleep 2
