@echo off
setlocal

REM Run the MCP HTTP server on all interfaces for LAN access.
set MCP_HTTP_HOST=0.0.0.0
set MCP_HTTP_PORT=8001
python -m src.mcp_http_server
