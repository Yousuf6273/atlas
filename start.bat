@echo off
REM Double-click to install (first time only) and open ATLAS in your browser (Windows).
cd /d "%~dp0"
if not exist .venv\Scripts\atlas.exe (
  echo First run: setting up ATLAS...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -e .
  .venv\Scripts\atlas init
)
.venv\Scripts\atlas ui
