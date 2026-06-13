$env:MCP_HTTP_HOST = "0.0.0.0"
$env:MCP_HTTP_PORT = "8001"
# Prefer the project venv; fall back to system Python 3.13
$venvPy = "$PSScriptRoot\.venv\Scripts\python.exe"
$py = if (Test-Path $venvPy) { $venvPy } else { "C:\Users\colin\AppData\Local\Programs\Python\Python313\python.exe" }
& $py -m src.mcp_http_server
