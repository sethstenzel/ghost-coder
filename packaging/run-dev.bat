@echo off
rem Launch Ghost Coder from the project virtual environment without a console window.
cd /d "%~dp0.."
start "" .\.venv\Scripts\pythonw.exe -m ghost_coder %*
