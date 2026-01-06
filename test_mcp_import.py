#!/usr/bin/env python3
"""Quick test script to verify MCP server imports correctly."""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

print(f"Python: {sys.version}")
print(f"Project root: {project_root}")
print(f"Path includes: {project_root in [Path(p) for p in sys.path]}")

try:
    from src.mcp_server import ResearchMCPServer
    print("[OK] MCP server import OK")
except ImportError as e:
    print(f"[FAIL] Import error: {e}")

try:
    from src.pipeline import ResearchRAGPipeline
    print("[OK] Pipeline import OK")
except ImportError as e:
    print(f"[FAIL] Pipeline import error: {e}")

try:
    import mcp
    print(f"[OK] MCP package found")
except ImportError as e:
    print(f"[FAIL] MCP package not installed: {e}")
