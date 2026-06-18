#!/usr/bin/env bash
# Helper actions for the Re-Searcher systemd stack (see docs/OPERATIONS_SPARKY.md).
#
# The systemd user units call these subcommands; you can also run them by hand.
# Everything is config-driven from config.yaml — endpoints/ports are read from there
# so this stays in sync with the pipeline.
#
#   researcher_svc.sh wait-backends   # block until Chroma + vLLM embed are ready
#   researcher_svc.sh serve-up        # expose MCP on the tailnet via tailscale serve
#   researcher_svc.sh serve-down      # remove the tailnet serve mapping
#   researcher_svc.sh warmup          # one query to get past the cold-start latency
#   researcher_svc.sh status          # human-readable health of the whole stack
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
CONFIG="${RESEARCHER_CONFIG:-$ROOT/config.yaml}"

# --- read endpoints/ports from config (single source of truth) ---------------
read_cfg() { "$PY" - "$CONFIG" "$1" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
def get(path):
    cur = cfg
    for k in path.split("."):
        cur = (cur or {}).get(k, {})
    return cur
print(get(sys.argv[2]) or "")
PY
}

CHROMA_ENDPOINT="$(read_cfg storage.endpoint)";           CHROMA_ENDPOINT="${CHROMA_ENDPOINT:-http://localhost:8000}"
VLLM_EMBED_URL="$(read_cfg embedding.vllm.base_url)";      VLLM_EMBED_URL="${VLLM_EMBED_URL:-http://localhost:8002/v1}"
MCP_HOST="${MCP_HTTP_HOST:-127.0.0.1}"
MCP_PORT="${MCP_HTTP_PORT:-8001}"
TS_CONTAINER="${TAILSCALE_CONTAINER:-tailscale}"

ok()   { printf '  [OK] %s\n' "$1"; }
warn() { printf '  [--] %s\n' "$1"; }

# --- wait for the data + embedding backends to answer ------------------------
wait_backends() {
  local deadline=$(( $(date +%s) + ${WAIT_TIMEOUT:-180} ))
  echo "[svc] waiting for Chroma ($CHROMA_ENDPOINT) and vLLM embed ($VLLM_EMBED_URL) …"
  while :; do
    local c=1 v=1
    curl -fsS -m 3 "$CHROMA_ENDPOINT/api/v2/heartbeat" >/dev/null 2>&1 && c=0
    curl -fsS -m 3 "$VLLM_EMBED_URL/models"            >/dev/null 2>&1 && v=0
    [ $c -eq 0 ] && [ $v -eq 0 ] && { echo "[svc] backends ready."; return 0; }
    [ "$(date +%s)" -ge "$deadline" ] && { echo "[svc] ERROR: backends not ready in time (chroma=$c vllm=$v)"; return 1; }
    sleep 3
  done
}

# --- expose the MCP HTTP server on the tailnet -------------------------------
# Userspace tailscale (TUN:false) can't bind the 100.x IP directly, so we proxy
# the tailnet port to the loopback MCP server — same pattern as :3000 and :8000.
serve_up() {
  echo "[svc] tailscale serve: http://<tailnet>:$MCP_PORT -> http://$MCP_HOST:$MCP_PORT"
  docker exec "$TS_CONTAINER" tailscale serve --bg --http="$MCP_PORT" "http://$MCP_HOST:$MCP_PORT"
}
serve_down() {
  echo "[svc] tailscale serve: removing port $MCP_PORT"
  docker exec "$TS_CONTAINER" tailscale serve --http="$MCP_PORT" off 2>/dev/null \
    || warn "serve mapping for $MCP_PORT not present"
}

# --- warm the query path past the cold-start (embedder JIT + index load) -----
warmup() {
  echo "[svc] warmup query (one search to absorb the ~50s cold start) …"
  "$PY" scripts/query.py "coaching process" -k 3 --no-rerank --json >/dev/null 2>&1 \
    && echo "[svc] warmup done." || warn "warmup query failed (stack may still be starting)"
}

status() {
  echo "Re-Searcher stack status:"
  curl -fsS -m 3 "$CHROMA_ENDPOINT/api/v2/heartbeat" >/dev/null 2>&1 && ok "Chroma   $CHROMA_ENDPOINT" || warn "Chroma   $CHROMA_ENDPOINT (down)"
  curl -fsS -m 3 "$VLLM_EMBED_URL/models"            >/dev/null 2>&1 && ok "vLLM embed $VLLM_EMBED_URL" || warn "vLLM embed $VLLM_EMBED_URL (down)"
  "$PY" scripts/vllm_service.py status 2>&1 | sed 's/^/  /'
  curl -fsS -m 3 "http://$MCP_HOST:$MCP_PORT/healthz" >/dev/null 2>&1 && ok "MCP      http://$MCP_HOST:$MCP_PORT" || warn "MCP      http://$MCP_HOST:$MCP_PORT (down)"
  docker exec "$TS_CONTAINER" tailscale serve status 2>/dev/null | grep -q ":$MCP_PORT" \
    && ok "tailnet  MCP served on port $MCP_PORT" || warn "tailnet  MCP not served"
}

case "${1:-}" in
  wait-backends) wait_backends ;;
  serve-up)      serve_up ;;
  serve-down)    serve_down ;;
  warmup)        warmup ;;
  status)        status ;;
  *) echo "usage: $0 {wait-backends|serve-up|serve-down|warmup|status}" >&2; exit 2 ;;
esac
