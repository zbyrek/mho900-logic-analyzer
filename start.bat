@echo off
rem Starts the program from its virtual environment, wherever this file is called from.
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
    echo No .venv in %CD%. Create it with:
    echo   py -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"
python main.py %*
if errorlevel 1 pause
