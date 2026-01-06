import mcp
print("MCP version:", getattr(mcp, '__version__', 'unknown'))
print("MCP contents:", dir(mcp))

try:
    from mcp.server import Server
    print("[OK] mcp.server.Server found")
except ImportError as e:
    print(f"[FAIL] mcp.server import error: {e}")

try:
    from mcp import Server
    print("[OK] mcp.Server found (direct)")
except ImportError as e:
    print(f"[FAIL] mcp.Server direct import error: {e}")

# Check what submodules exist
import pkgutil
print("\nMCP submodules:")
for importer, modname, ispkg in pkgutil.iter_modules(mcp.__path__):
    print(f"  - {modname} (package={ispkg})")
