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

"%PYTHON%" scripts\warmup.py %*
set "WARMUP_EXIT=%ERRORLEVEL%"

echo.
if not "%RESEARCHER_WARMUP_NO_PAUSE%"=="1" pause

exit /b %WARMUP_EXIT%
