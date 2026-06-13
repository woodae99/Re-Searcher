@echo off
setlocal
set ROOT=C:\Users\colin\Dev\GitHub\Re-Searcher
REM Prefer the project venv; fall back to system Python 3.13
if exist "%ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
) else (
    set "PYTHON=C:\Users\colin\AppData\Local\Programs\Python\Python313\python.exe"
)
set OUT=%ROOT%\output\prod_ingest_bg.out.log
set ERR=%ROOT%\output\prod_ingest_bg.err.log

cd /d %ROOT%
"%PYTHON%" scripts\index.py --plain-progress --force --collection research_library 1>"%OUT%" 2>"%ERR%"
