#!/bin/bash
# Double-click to stop ATLAS (macOS/Linux).
if pkill -f "streamlit run .*atlas/app/streamlit_app.py"; then echo "ATLAS stopped."; else echo "ATLAS was not running."; fi
sleep 2
