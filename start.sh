#!/bin/bash
# Double-click this file (macOS) to install (first time only) and open ATLAS in your browser.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/atlas ]; then
  echo "First run: setting up ATLAS (takes a minute)…"
  python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e . || { echo "Setup failed — is Python 3.11+ installed?"; read -r; exit 1; }
  .venv/bin/atlas init
fi
exec .venv/bin/atlas ui
