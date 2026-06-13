@echo off
set PYTHONPATH=C:\Users\colin\Dev\GitHub\Re-Searcher
set MCP_DEBUG_LOG=C:\Users\colin\ChromaData\mcp-debug.log
set RERANK_DEBUG_LOG=C:\Users\colin\ChromaData\rerank-debug.log
cd /d C:\Users\colin\Dev\GitHub\Re-Searcher

REM Prefer the project venv; fall back to system Python 3.13
if exist "%CD%\.venv\Scripts\python.exe" (
    set "PYTHON=%CD%\.venv\Scripts\python.exe"
) else (
    set "PYTHON=C:\Users\colin\AppData\Local\Programs\Python\Python313\python.exe"
)
"%PYTHON%" src\mcp_server.py %*
