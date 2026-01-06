@echo off
set PYTHONPATH=C:\Users\colin\Dev\GitHub\Re-Searcher
cd /d C:\Users\colin\Dev\GitHub\Re-Searcher

REM Use the explicit Python path where mcp is installed
C:\Users\colin\AppData\Local\Programs\Python\Python313\python.exe src\mcp_server.py %*
