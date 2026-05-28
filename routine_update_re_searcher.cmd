@echo off
setlocal

cd /d "%~dp0"
set "PYTHONPATH=%CD%"

if exist "%CD%\.venv\Scripts\python.exe" (
    set "PYTHON=%CD%\.venv\Scripts\python.exe"
) else if exist "C:\Users\colin\AppData\Local\Programs\Python\Python313\python.exe" (
    set "PYTHON=C:\Users\colin\AppData\Local\Programs\Python\Python313\python.exe"
) else (
    set "PYTHON=python"
)

echo Re-Searcher routine update starting
echo Config: %CD%\config.yaml
echo Mode: incremental/routine update
echo.

"%PYTHON%" scripts\index.py --plain-progress
set "UPDATE_EXIT=%ERRORLEVEL%"

echo.
if "%UPDATE_EXIT%"=="0" (
    echo Routine update complete.
) else (
    echo Routine update failed with exit code %UPDATE_EXIT%.
)
echo.

if not "%RESEARCHER_UPDATE_NO_PAUSE%"=="1" pause

exit /b %UPDATE_EXIT%
