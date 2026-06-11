---
type: bug-report
for: codex
component: Re-Searcher MCP — list_sources background cache
created: 2026-06-10
severity: blocks full-register enumeration (get_source_chunks unaffected)
---
# Bug report for Codex — `list_sources` background cache never completes

## One-line

`list_sources` returns the "cache is building in the background, retry later" message **on every call, indefinitely** — it never transitions to a populated result. `get_source_chunks` against the same live server is instant and correct, so ChromaDB is healthy; the fault is in the Bug-2 background-cache path you added, which was the one path you noted you did **not** live-run on the production collection.

## Environment (note the gap that probably hides the bug)

- **Live MCP server under test:** `http://100.78.17.127:8001/mcp/` (host **sparky**). This is what the thesis tooling actually calls.
- **Where you verified the fix:** Windows dev checkout (`.\.venv\Scripts\python.exe -m pytest …`) + a live `get_source_chunks` smoke. You explicitly did *not* live-run `list_sources` on the huge collection.
- So the cache-build path has only ever run under pytest (likely with a small/mocked collection), never against the real Zotero collection on sparky. **First thing to confirm: is the code running on sparky actually the fixed build, and is its `output/` dir writable by the server process?**

## Exact reproduction (observed 2026-06-10 ~16:39 UTC, after a full reboot + re-warm of sparky)

MCP calls and verbatim responses:

```
list_sources(limit=3)
→ "Source register cache is building in the background. This collection is large,
   so the first cold list_sources call returns immediately to avoid MCP client
   timeouts. Retry list_sources after the cache build completes; subsequent calls
   use the persisted cache."

list_sources(limit=3, source_type="obsidian")     # much smaller subset
→ same "cache is building" message

list_sources(limit=3)                               # repeated minutes later
→ same "cache is building" message
```

Contrast — the per-source path is fine on the same server, same moment:

```
get_source_chunks(zotero_key="5WPQDBL5", include_text=false, limit=2)
→ returns 6 chunks instantly, correct metadata
get_source_chunks(zotero_key="WNTGERMP", …)  → 30,848 chunks, instant
```

Timeline: the "building" message has persisted across **two separate warm periods** — once for ~20+ min before the reboot (Colin reports **no disk thrashing** during that window, i.e. it did not look like a scan was actually running), and again after a reboot + deliberate re-warm. It is not a slow build; it appears to never finish or to die silently.

## Why this points at the cache path, not Chroma

- `get_source_chunks` does a metadata `get` on Chroma per source and works → Chroma connection, collection handle, and metadata are all healthy.
- `list_sources` with `source_type="obsidian"` (a small slice) also stays "building" → the gate is global, returned **before** any per-type work, so the size of the requested subset is irrelevant. The background task is gating everything and never clears its flag.

## Hypotheses to check (ranked), mapped to your Bug-2 description

You described `list_sources` as: load persisted cache from `output/mcp_source_cache.json` when valid; persist successful builds keyed by `collection.count()`; for large collections with no valid cache, start the cold scan in a background task and return immediately with a retry message.

1. **The background task never actually runs to completion / dies silently.** If it's launched with `asyncio.create_task(...)` inside a request-scoped event loop (FastMCP per-request), the task can be cancelled/GC'd when the request returns — so it never finishes, and every subsequent call sees "no valid cache" and re-launches a task that also dies. *Symptom match: no disk thrash, never completes, survives reboot.* Check: is the task held by a strong reference on a long-lived object (module/server singleton), or only a local var? Is it on the server's main loop or a per-request loop?
2. **Completion flag / cache write never happens (or writes somewhere unreadable).** If the scan completes but `output/mcp_source_cache.json` is never written (path relative to a different CWD on sparky, or dir not writable by the service user), the "valid cache" check fails forever. Check: does the file exist on sparky after a warm period? What CWD does the systemd/service unit give the process? Is `output/` absolute or relative?
3. **Cache-key mismatch.** Keyed on `collection.count()` — if the count read at write-time differs from read-time (e.g. count is over a different collection, or includes/excludes sub-collections inconsistently), validity never matches. Check: log both the key written and the key compared.
4. **An exception inside the background coroutine is swallowed.** A bare task with no exception handler will fail silently. Check: wrap the build in try/except that logs to the server log, and look at the sparky log after a cold call.

## Diagnostics to run on sparky (not just pytest)

```bash
# on sparky, against the live service:
ls -la <re-searcher>/output/mcp_source_cache.json     # does it ever appear? mtime?
# trigger a cold call, then immediately watch the server log:
tail -f <server log>                                  # look for: task started, exceptions, "persisted cache" write
# confirm the running process is the fixed build:
#   git rev-parse HEAD in the deployed checkout, vs the commit with the Bug-2 fix
# confirm CWD / writability of output/ for the service user
systemctl show <unit> -p WorkingDirectory ; ls -ld <re-searcher>/output
```

Then add temporary logging at four points in the `list_sources` cache path: (a) cold-call decision "no valid cache → launching build", (b) background task entry, (c) background task completion + bytes written, (d) every cache-validity comparison (key_written vs key_now). One cold call should then tell you exactly which of the four hypotheses it is.

## A pytest gap worth closing

Add a test that exercises the background path **end to end on a real (or realistically sized) persistent Chroma**, asserting that after the build completes a subsequent `list_sources` returns populated results and that `output/mcp_source_cache.json` exists with the expected key. The current tests apparently cover "cache rebuild" logic but not the create-task-survives-and-persists behavior against a live loop — which is exactly where this is failing.

## Definition of done

- A cold `list_sources` on the sparky production collection, given a reasonable wait, returns a populated source list; a second call returns immediately from `output/mcp_source_cache.json`.
- The cache file exists on sparky with an mtime from the build, keyed correctly.
- A regression test covers the create-task → complete → persist → serve-from-cache cycle.

## Aside (not this bug, but spotted)

`5WPQDBL5` (Myers, "Researching the coaching process") was earlier reported by the register pull as "no full text", but `get_source_chunks` shows it has 6 `zotero_fulltext` mid chunks. That discrepancy is in the register-build heuristic, not the cache — tracking separately.
