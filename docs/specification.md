# Semantic Search App – Technical & UI Specification

## 0 · Purpose
Provide researchers with fast, offline, semantic + keyword search across local documents and a Zotero library, wrapped in a minimalist UI that surfaces the most relevant passages first.

## 1 · Target environment
Primary OS: Windows 11 workstation (offline‑capable)

Secondary dev: Ubuntu 22.04 VM (same codebase)

Data roots:

C:\Users\<you>\Zotero\ – local Zotero DB + storage/ tree
One or more watch folders (Markdown notes, DOCX, slides, etc.)
Python 3.11 virtual‑env in project folder → portable between machines

## 2 · Functional requirements
Ref	Function	Notes
FR‑1	Global search box (⌘/Ctrl + K)	Accepts keyword and natural‑language questions
FR‑2	Boolean / field operators	author:Smith AND year:2020, "exact phrase", NOT tag:draft …
FR‑3	Semantic similarity ranking	Approx‑nearest‑neighbour (FAISS) on e5‑large embeddings + BM25 mix
FR‑4	Faceted filters	Zotero tags, collections, year, file‑type checkboxes
FR‑5	Inline passage preview	Highlight the matched span; click → open doc/PDF at page via zotero://select/items/... or local path
FR‑6	Multi‑source ingestion	Local folders and Zotero attachments/notes; deduplicate by SHA‑1
FR‑7	Incremental re‑index	Skip run when MAX(dateModified) unchanged; nightly scheduler
FR‑8	Offline first	No cloud calls; embeddings computed locally (CPU fallback)
FR‑9	Configurable thresholds	Top‑k results, chunk size, semantic/keyword weight slider
FR‑10	Export / cite	Copy BibTeX or Zotero citekey from hit
## 3 · Non‑functional requirements
Performance: <150 ms query → top‑N results after warm cache
Storage: Embedding index ≤ 3× original text size (quantised vectors)
Portability: Single project folder copyable to laptop (venv + data)
Resilience: Read‑only SQLite access; handle database is locked with retry
Security: No data leaves device; WebDAV share mounted read‑only
## 4 · Architecture overview
┌────────────┐   crawl   ┌──────────┐   extract   ┌──────────┐  embed  ┌──────────┐
│  Watchers  │──────────▶│ Extractor│────────────▶│ Chunker  │────────▶│ Embedder │
└────────────┘           └──────────┘             └──────────┘         └────┬─────┘
                                                                            │ vectors
                                   metadata                                 ▼
                              ┌────────────────┐                      ┌───────────┐
User query ▶ Parser ▶ Embed ▶ │  Search Core   │──top‑k IDs──────────▶│  UI Layer │
                              └────────────────┘                      └───────────┘
Components

Extractor – PDF (pymupdf), DOCX (python‑docx), HTML→MD (html2text)
Chunker – sliding window 400 token chunks (overlap 20 %)
Embedder – intfloat/e5-large-v2 on CPU/GPU; quantise to int8
Vector store – FAISS IVF‑PQ; sidecar SQLite for metadata
Search core – hybrid BM25 + vector fusion (reciprocal‑rank)
Scheduler – Windows Task Scheduler or cron for nightly reindex
## 5 · Data model
Document:
  id: str # namespaced (folder|zotero-note|zotero-pdf)
  source_path: str # file path or zotero:// link
  title: str
  authors: list[str]
  year: int | None
  tags: list[str]
  collections: list[str]
  text: str # full plain‑text
Chunk:
  doc_id: str
  chunk_id: int
  vector: ndarray
## 6 · APIs & integration points
`` – yields (logical_id, TextOrNone, metadata)
`` – existing function unchanged
``** (FastAPI)** – JSON {q, filters} → returns ranked hits
`` – triggers ingest pipeline (blocking)
## 7 · UI specification (Streamlit prototype → React later)
Layout
┌─────────────────────────────────────────────────────────────────────────────┐
│  🔍  [ Search… ]   [☰ Advanced]                  (  ∑ results | ⟳ status ) │
├───────────────┬─────────────────────────────────────────────────────────────┤
│  Filters      │  Results list (scroll)                                      │
│  ▸ Type       │  ┌───────────────────────────────────────────────────────┐  │
│  ▸ Year       │  │ Title – score – tags                                 │  │
│  ▸ Tags       │  │   …highlighted passage…                              │  │
│  ▸ Collections│  │ [Open] [Copy citekey]                                 │  │
│               │  └───────────────────────────────────────────────────────┘  │
└───────────────┴─────────────────────────────────────────────────────────────┘
Search bar always visible; Enter triggers hybrid query.
Advanced panel → boolean builder UI, weight sliders, chunk size.
Real‑time filters update results without re‑embedding.
Result click opens right‑side preview drawer (PDF page image or note markdown).
Styling cues
Neutral dark‑on‑light palette; accent colour on highlights.

Mono‑space font for passages; serif for titles.

Keyboard shortcuts:

Ctrl+K – focus search
Esc – close preview drawer
## 8 · Open questions / next iteration
GPU vs CPU embeddings: decide based on workstation hardware.
DocX→text quality: keep simple strip or use a richer converter?
UI framework: keep Streamlit or move to Tauri/Electron for a native feel?
Sharing indexed data between machines: pack index in a portable folder or regenerate per device?
## 9 · External API & Integrations
### 9.1 Public REST endpoint (FastAPI)
Method	Path	Purpose
GET	/api/search	Query index (q, filters, top_k, `mode=semantic	keyword	hybrid`)
POST	/api/search	Same as above but JSON body (larger queries)
GET	/api/doc/{doc_id}	Full metadata & optional full‑text
GET	/api/status	Embedder/version, index size, last‐built timestamp
All responses are JSON. Search returns an array of ** where ** holds **, **, **, **, ``.

### 9.2 Security model
Auth: static API keys (X-API-Key header) stored in .env; checked by FastAPI dependency.
CORS: restricted to localhost, LAN CIDR, and optional DDNS domain.
Network exposure: run behind WireGuard/Tailscale or expose port 8000 via router only when desired.
Rate limits: 30 req/min default via slowapi middleware.
### 9.3 LLM‑friendly helpers
/api/summarise?q=... → returns single Markdown summary ≤800 tokens (chunks → LangChain map‑reduce) so ChatGPT can ingest.
/api/embed internal‑only; allows future "+RAG" pipelines.
### 9.4 Obsidian plugin sketch
JS plugin calls /api/search?q=$selection → shows modal picker.
On hit choose Insert link → [title](zotero://select/items/...) or local path.
Settings pane for endpoint URL + API key.
### 9.5 ChatGPT integration pattern
Provide ChatGPT with endpoint + key via environment variables in a custom function calling wrapper.
Agent logic: on user question → call /api/summarise → feed summary + citations back into the conversation.
Optional lambda proxy to scrub private paths before returning to LLM.
## 10 · Repository & DevOps
### 10.1 GitHub repo layout
semantic-search-app/
├─ src/                # Python packages
├─ ui/                 # Streamlit (or React) frontend
├─ tests/
├─ docs/               # MkDocs for user guide
├─ docker/             # Dockerfile & compose for server deploy
├─ .github/workflows/  # CI – lint, unit tests, build image
└─ pyproject.toml      # Poetry deps / version
### 10.2 Workflows
CI: black + isort + mypy → pytest → build + push ghcr.io/<user>/semantic-search image.
CD (optional): self‑hosted runner on workstation triggers docker compose pull & up -d after successful build.
Versioning: Semantic (MAJOR.MINOR.PATCH). Tag releases; GH Actions attach built artefacts.
Secrets: API keys & embedding models kept in repo‑level Actions secrets; never commit .env.
### 10.3 Contribution guidelines
Feature branches → PR → code review (GitHub).
Conventional commits + CHANGELOG.md automated via release‐please.
Issue templates for bug / feature / discussion.
