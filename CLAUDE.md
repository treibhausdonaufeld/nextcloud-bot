# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A community/association ("Verein") automation bot around a Nextcloud instance. It syncs Nextcloud Collectives wiki pages, parses them into structured data (groups, protocols, decisions), sends notifications (Rocket.Chat, mail), and serves analytics dashboards. Most UI strings and parsed content are German.

## Commands

Dependency management is via `uv` (Python 3.13, see `.python-version`).

```bash
uv sync --dev                     # install all deps incl. dev group
uv run pytest                     # run tests
uv run pytest tests/test_group_parsing.py                         # single file
uv run pytest tests/test_protocol_decision_parsing.py::TestProtocolDecisionExtraction::test_extract_single_decision  # single test
uv run pytest --cov=app --cov-report=html --cov-report=term       # coverage (source = app/ only)
pre-commit run --all-files        # lint: ruff + ruff-format + mypy (mypy.ini) + prettier + misc hooks
uv sync -U                        # upgrade packages
```

Running locally (no external services needed; the SQLite file defaults to `data/nextcloud_bot.db`, override with `DATABASE_URL`):

```bash
uv run uvicorn app.main:app --reload    # web UI + background worker on :8000
WORKER_ENABLED=false uv run uvicorn app.main:app --reload   # UI only
uv run python cli.py sync               # one manual sync/notify iteration
uv run python cli.py sync --update-all  # re-fetch and re-parse all pages
uv run python cli.py sync --update-pages 1,2
uv run python cli.py clear-parsed-data
uv run python cli.py import-xlsx decisions.xlsx
```

Translations (gettext via Babel, German is the only translated locale; extraction covers `*.py` and `app/templates/*.html`, see `babel.cfg`):

```bash
make update_po                    # extract strings into locales/*.po (needs gettext tools)
make compile                      # compile .po -> .mo (required for changes to show up)
```

CI (`.github/workflows/docker-build.yml`) runs pre-commit, pytest, and builds/pushes a Docker image on every push.

## Architecture

One container runs everything. `app/main.py` builds the Ravyn app: routes, Jinja2 templates (`app/templates/`), vendored static assets (`app/static/`: Pico.css, htmx, plotly-basic, vis-network — no CDN), and an `on_startup` hook that initializes the database and spawns the background worker as an asyncio task.

- **Controllers** (`app/controllers/`) are synchronous handlers (Ravyn runs them in a threadpool): dashboard (FTS search), groups (vis-network org chart), timeline (plotly from markdown tables on a Collectives page), protocols, logbook (decision cards + XLSX import), mentions (table + network graph). Graph pages fetch JSON endpoints (`/groups/graph.json`, `/mentions/graph.json`) and load click-detail partials via htmx.
- **Worker** (`app/worker.py`) is the former runner loop: update user list from Nextcloud → fetch changed Collectives pages → parse groups/protocols → periodic tasks (avatar fetching via Pillow, mailinglist distribution, calendar/deck reminders). It runs sync code via `asyncio.to_thread` and sleeps according to `sleep_minutes`/quiet hours from the bot config.
- **`cli.py`** is a Click CLI for one-off operations (manual sync, clear parsed data, XLSX import).

### Storage — SQLite via Edgy, with a sync bridge

`app/db.py` owns the Edgy registry and a **dedicated database event-loop thread**: Edgy is async-first but the parsing pipeline/worker are sync, so ALL database access goes through `run_db(coro)`, which dispatches to that single loop (one connection pool, serialized SQLite writes, WAL mode). Never await Edgy queries on the server loop directly.

`app/models/base.py` defines `BaseDBModel`: sync facade methods `store()`/`remove()`/`fetch()`/`fetch_one()`/`count()`. Conventions:

- `natural_key_fields` makes `store()` upsert by natural key (e.g. `page_id`, `username`, Decision's computed `natural_key`) instead of inserting duplicates.
- Persistence side effects go in `after_store()`/`before_remove()` hooks — `CollectivePage` rebuilds the `mentions` table and the FTS index there and cascades deletes to Protocol/Decision; `Decision` maintains its own FTS entry. Keep these hooks in sync when adding indexed content.
- `__init__` materializes field defaults eagerly (Edgy leaves them unset until insert), so fresh instances support plain attribute access.

Full-text search is a raw FTS5 virtual table `search_index` (`doc_type` ∈ page|decision), maintained in the store/remove hooks and queried via `app.db.search()` (unicode61 tokenizer, prefix matching, weighted bm25 ranking, `snippet()` highlighting). German is handled by `app/textnorm.py`: a `lemmas` FTS column carries lemmatized tokens and dictionary-based compound-word splits (both via simplemma), and `fts_escape()` expands query terms the same way — so "Gießkannen"/"gekauft" match "Gießkanne"/"kaufen" and "Geräte" matches "Gartengeräte". Decisions are indexed with their context (agenda heading above the `::: success` block, objections, group, date). `_migrate_schema()` in `app/db.py` upgrades older databases (new columns, FTS rebuild) at startup. The `mentions` table replaces the old CouchDB map/reduce view; `KVState` is a small key-value store for calendar/deck processed-item state.

### Configuration — two layers

1. **Env vars** → `app/settings.py` (pydantic-settings, nested delimiter `__`, e.g. `NEXTCLOUD__BASE_URL`). Infrastructure: URLs, credentials, Sentry, `DATABASE_URL`.
2. **Runtime bot config lives in Nextcloud itself**: `BotConfig.load_config()` (`app/services/config.py`) fetches a Collectives page (`configuration_page_id`) and parses a YAML block out of it. This holds all the keyword lists that drive markdown parsing (group prefixes, protocol/decision/moderation keywords), notification channel mappings, cooldowns, quiet hours. `config.example.yml` documents the shape. Parsing behavior changes are usually config-keyword changes, not code changes.

### Parsing pipeline

`app/services/collectives_loader.py` fetches page metadata via the Nextcloud OCS Collectives API and raw markdown via WebDAV (admin Basic auth), upserting `CollectivePage` rows. `collectives_parser.py` classifies pages by title/path into group/protocol subtypes; the models' `update_from_page()` methods do the actual keyword-driven markdown parsing. Protocols extract decisions from `::: success ... :::` blocks into `Decision` rows (deleting a protocol/page cascades to its decisions). User mentions everywhere use `user_regex` from `app/settings.py` (`mention://user/<name>`).

### Protocol versioning & media

Protocol pages are versioned self-contained in the bot database: on every content change during sync, `ProtocolVersion.record()` stores a full markdown snapshot, a unified diff to the previous version and the editing user (Nextcloud `lastUserId`); `app/services/protocol_media.py` copies referenced `.attachments.<id>/` files to the media folder (env var `MEDIA_FOLDER`, default `/data/media`) laid out as `YYYY/MM/DD/<page-id>/attachments/<folder-id>/<name>` (date = protocol date) so old attachments can be pruned by date; `ProtocolMedia` rows hold the metadata, and a pruned file just 404s without being re-downloaded. Both are hooked into `store_pages` and (for backfill) `parse_protocols` and are idempotent. The protocols page opens an in-app popup (`app/controllers/protocol_view.py`, `partials/protocol_view.html`) that renders the markdown (`app/services/protocol_render.py`: `::: x :::` callouts, `mention://` links, media links rewritten to `/protocols/{page_id}/media/{folder-id}/{name}`, output sanitized with nh3), lists the version history with per-version diffs and shows each version's raw markdown for manual restoring — the app itself never writes back to Nextcloud. `delete_orphaned_pages` never deletes protocol pages older than 7 days (`PROTOCOL_DELETE_PROTECTION_DAYS`), so protocols that vanish from Nextcloud keep their history here; renames are unaffected since the page_id stays stable.

### i18n

User-facing strings in Python must be wrapped with `_()` (or `_n()`) from `app.settings`; templates get `_`/`_n` passed in explicitly via `app/i18n.py::template_context` (the request language comes from the `lang` cookie or Accept-Language). The gettext catalog is stored in a ContextVar, so per-request switching is thread-safe; `locale.setlocale` is process-global and only set once at startup. After adding/changing strings run `make update_po`, translate in `locales/de/LC_MESSAGES/messages.po`, then `make compile`.

## Tests

Tests run without a database: they mock `bot_config` and patch `store`/`remove`/`fetch` on the models, so the Edgy registry never connects. `tests/conftest.py` resets the class-level caches (`Group._cached_groups`, `NCUserList._cached_users`) between tests. Coverage focuses on markdown parsing (`Group.update_from_page`, `Protocol.extract_decisions`) and the calendar notifier; loaders, mail, and controllers are untested.
