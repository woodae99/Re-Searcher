$env:MCP_HTTP_HOST = "0.0.0.0"
$env:MCP_HTTP_PORT = "8001"
& "$PSScriptRoot\.venv\Scripts\python.exe" -m src.mcp_http_server
