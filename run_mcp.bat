@echo off
set PYTHONPATH=C:\Users\colin\Dev\GitHub\Re-Searcher
set MCP_DEBUG_LOG=C:\Users\colin\ChromaData\mcp-debug.log
set RERANK_DEBUG_LOG=C:\Users\colin\ChromaData\rerank-debug.log
cd /d C:\Users\colin\Dev\GitHub\Re-Searcher

REM Use the explicit Python path where mcp is installed
C:\Users\colin\AppData\Local\Programs\Python\Python313\python.exe src\mcp_server.py %*
