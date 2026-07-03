# Refactor Plan: Streamlit/CouchDB → Ravyn/SQLite (single container)

## Goal

Replace the current four-container stack (Streamlit UI, runner worker, CouchDB, ChromaDB
— plus imaginary) with **one container** running a single [Ravyn](https://www.ravyn.dev/)
application that serves the web UI, runs the background sync/notification loop in-process,
and stores everything in a **SQLite** file. The embedding/RAG stack (ChromaDB, langchain
text splitters, embedding functions) is dropped for now; text search is served by SQLite
FTS5 instead.

## Target stack

| Concern | Today | Target |
| --- | --- | --- |
| Web framework | Streamlit (`app.py`, `pages/*`) | **Ravyn** (`pip install ravyn[standard]`), sync+async handlers, served by uvicorn |
| ORM / storage | CouchDB via `pycouchdb` + custom `CouchDBModel` | **Edgy** (Ravyn's native ORM, async-first, Django-like queries, Alembic-based migrations) on **SQLite** |
| Semantic search | ChromaDB + Gemini/HF embeddings | **SQLite FTS5** virtual table (keyword/full-text); Gemini Q&A stays optional, fed by FTS5 retrieval instead of vector retrieval |
| Frontend | Streamlit widgets, plotly, streamlit-agraph | **Jinja2 templates + htmx** (partial updates, forms, search-as-you-type) + **Pico.css** for styling; vendored **plotly.js** for the timeline chart and **vis-network** for the group/mention graphs (same library streamlit-agraph wraps) — all static assets served by Ravyn, no CDN |
| Background worker | separate `datafetcher` container running `runner.py --loop` | in-process **asyncz scheduler** (`ravyn[schedulers]`) or a lifespan background task inside the same app |
| Avatar conversion | external `imaginary` container | **Pillow** in-process (JPEG conversion is the only feature used) |
| Containers | datafetcher, analytics, couchdb, chromadb, imaginary | **one** service; volume for the SQLite file + avatars |

Why Edgy and not SQLModel/SQLAlchemy: it is the ORM Ravyn integrates natively (registry
plugs into the app lifecycle), its query API (`Model.query.filter(...)`) maps almost 1:1
onto the existing `get_all(selector=...)`/`get_by(...)` calls, and it ships migrations.
The existing models are already Pydantic, so field definitions carry over with minimal
changes.

## Data model mapping

`CouchDBModel` (type-discriminated JSON docs, Mango `_find`) becomes one Edgy model per
document type. Nextcloud remains the source of truth, so **no CouchDB data migration is
needed** — after deploying, run a full re-sync (`--update-all`) to rebuild the database.
The only data not reproducible from Nextcloud are manually imported logbook decisions;
re-import them via the existing XLSX upload.

| CouchDB doc (`type`) | SQLite table | Notes |
| --- | --- | --- |
| `CollectivePage` | `collective_pages` | flatten the nested `ocs` object into columns (id, title, filePath, timestamp, …); keep `content` TEXT; index `(timestamp)` |
| `Group` | `groups` | members/coordination/delegates as JSON columns (or a `group_members` join table if querying by user becomes common) |
| `Protocol` | `protocols` | FK → `collective_pages`; participants as JSON |
| `Decision` | `decisions` | FK → `protocols`/`collective_pages` (nullable for XLSX imports); index `(group_name, date)` |
| `NCUser` | `users` | flatten `OCSUser`; keep `enabled` flag logic from `NCUserList.update_from_nextcloud()` |
| `_design/mentions` JS view | `mentions` table `(page_id, username, count)` | populated at page-save time by running `user_regex` over content — replaces the map/reduce view queried by `pages/mentions.py` and `pages/groups.py` |
| raw docs `calendar_notifier_events`, `deck_reminder_cards` | `kv_state` table `(key TEXT PK, value JSON)` | tiny key-value store; removes the ad-hoc `couchdb().get/save` calls |
| ChromaDB collection | `page_search` FTS5 virtual table over pages + decisions | rebuilt/updated in the same save path that used to upsert embeddings |

CouchDB-specific machinery that simply disappears: `_rev` conflict retry, Mango index
bootstrapping, the in-process LRU instance cache (SQLite is local and fast; drop it
unless profiling says otherwise).

## New project layout

```
app/
  main.py            # Ravyn() app: routes, template config, static files, scheduler, lifespan
  db.py              # Edgy registry, engine (sqlite+aiosqlite:///data/bot.db), FTS5 setup
  models/            # Edgy models: collective_page.py, group.py, protocol.py, decision.py, user.py, kv.py
  controllers/       # one module per current page: dashboard, groups, timeline, protocols, logbook, mentions
  services/          # moved from lib/: collectives_loader, collectives_parser, avatar_fetcher,
                     # calendar_notifier, deck_reminder, mail/, outbound/, logbook_xlsx_import
  worker.py          # the runner-loop logic as a scheduled job (quiet hours, sleep_minutes from BotConfig)
  templates/         # Jinja2: base.html + one template per view + htmx partials
  static/            # pico.css, htmx.min.js, plotly.min.js, vis-network.min.js
  settings.py        # pydantic-settings (unchanged shape, minus couchdb/chromadb sections)
cli.py               # click CLI kept for one-off ops: sync --all/--pages, clear-parsed-data, import-xlsx
```

`lib/settings.py`'s i18n (`_()`, `set_language`) carries over; templates get the gettext
functions injected into the Jinja2 environment, and language selection moves from
`streamlit_js_eval` to `Accept-Language` header + a cookie set by the language switcher.

## Phases

### Phase 1 — Data layer (Edgy + SQLite)

1. Add `ravyn[standard,schedulers]`, `edgy[sqlite]`, `pillow`; remove `streamlit*`,
   `pycouchdb`, `chromadb`, `langchain-text-splitters`, `plotly` (python), `pandas`
   (keep only if the XLSX import stays on `openpyxl`+pandas), `streamlit-js-eval`,
   `streamlit-agraph`.
2. Create Edgy models per the mapping table; port `build_id()`-style deterministic IDs to
   natural keys / unique constraints (`collective_pages.page_id`, `users.username`).
3. Port query call-sites: `get_all(selector=..., sort=...)` → `Model.query.filter().order_by()`;
   `get_by(k, v)` → `Model.query.filter(**{k: v})`; `Decision.paginate()` → `limit/offset`.
4. Implement the `mentions` extraction in the page-save path and the `kv_state` helper for
   calendar/deck state.
5. Set up FTS5 (raw SQL migration; Edgy runs on SQLAlchemy so `text()` DDL is fine) with
   triggers or explicit re-index on page/decision save.
6. Strip all embedding code: delete `lib/chromadb.py`, remove chunk-upsert from
   `CollectivePage.save()`, embedding upsert/delete from `Decision`, `--clear-chromadb`
   from the CLI.

### Phase 2 — Ravyn app + background worker

1. Bootstrap `app/main.py`: Ravyn app with Jinja2 `TemplateConfig`, `StaticFilesConfig`,
   Edgy registry wired into the lifespan.
2. Move `runner.py`'s iteration logic into `app/worker.py` as an asyncz-scheduled job
   (interval re-computed from `BotConfig.sleep_minutes` + quiet hours, mirroring
   `calculate_sleep_duration`). The sync `requests`/`imaplib`/`caldav` code can stay sync
   — run it via `run_in_threadpool` so it doesn't block the event loop; converting the
   HTTP calls to `httpx.AsyncClient` is a later, optional cleanup.
3. Keep `cli.py` for one-off maintenance (force re-sync, clear parsed data, XLSX import)
   sharing the same service code.

### Phase 3 — Views (one per current Streamlit page)

| Route | Replaces | Rendering |
| --- | --- | --- |
| `GET /` | `app.py` dashboard | search form (htmx live results from FTS5), results table, optional streamed Gemini answer (SSE or chunked response) |
| `GET /groups` | `pages/groups.py` | vis-network org chart from a JSON endpoint; member details as htmx partial on node click |
| `GET /timeline` | `pages/timeline.py` | same markdown-table parsing, rendered with plotly.js (timeline/scatter) from a JSON endpoint |
| `GET /protocols` | `pages/protocols.py` | table + FTS5 search + optional Gemini summary |
| `GET /logbook` | `pages/logbook.py` | decision cards with pagination + filters (htmx), XLSX upload form |
| `GET /mentions` | `pages/mentions.py` | counts/graph from the `mentions` table (vis-network + table) |

Auth stays where it is today: the app itself is unauthenticated and the reverse proxy
(Caddy + oauth2 snippet in prod) terminates OAuth — no auth code needed in the app.

### Phase 4 — Packaging & CI

1. Dockerfile: `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`,
   healthcheck on a `/health` route.
2. `compose.yml` / `compose.prod.yml`: single `bot` service with a volume for
   `/data` (SQLite file, avatars); delete couchdb/chromadb/imaginary/datafetcher services
   and `COUCHDB__*`/`CHROMADB__*`/`IMAGINARY_URL` env vars.
3. `tests/conftest.py`: drop the `sys.modules` ChromaDB mocking; use an in-memory SQLite
   Edgy registry fixture instead. The parsing tests (groups, protocol decisions, calendar
   notifier) carry over nearly unchanged since they already mock persistence.
4. Update `mypy.ini` (remove pycouchdb/streamlit overrides), pre-commit, README, and this
   file's sibling `CLAUDE.md`.

### Deliberately out of scope (for now)

- Vector/semantic search and the embedding servers (FTS5 keyword search replaces it; the
  save-path hook point is kept so embeddings can return later).
- Async-ifying the Nextcloud/IMAP/CalDAV clients (threadpool is fine at this scale).
- Any auth inside the app (proxy-terminated, as today).

## Risks / notes

- **FTS5 vs. semantic search**: keyword search will feel different for the German-language
  Q&A ("Ask a question") flow; the `unicode61` tokenizer handles umlauts, but stemming is
  absent. Acceptable per "drop embeddings for now".
- **Single process** means a long-running sync iteration shares the event loop with web
  requests — hence the threadpool rule in Phase 2.
- **SQLite concurrency**: enable WAL mode; one writer (the worker) + read-mostly web
  handlers is well within SQLite's comfort zone.
- **Streamlit-only conveniences** (auto-rerun, `st.session_state`, cached resources) must
  be consciously replaced by explicit htmx interactions — the pages are read-mostly, so
  this is mostly straightforward templating.
