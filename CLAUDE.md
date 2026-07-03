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
uv run pytest --cov=lib --cov-report=html --cov-report=term       # coverage (source = lib/ only)
pre-commit run --all-files        # lint: ruff + ruff-format + mypy (mypy.ini) + prettier + misc hooks
uv sync -U                        # upgrade packages
```

Running the two entrypoints locally (both need services from `compose.yml`: CouchDB on :5984, ChromaDB on :8800, imaginary on :9001):

```bash
uv run streamlit run app.py       # web UI on :8501
uv run python runner.py           # one sync/notify iteration
uv run python runner.py --loop    # background worker loop
uv run python runner.py --update-all | --update-pages 1,2 | --clear-chromadb | --clear-parsed-data
```

Translations (gettext, German is the only translated locale):

```bash
make update_po                    # extract strings from *.py into locales/*.po
make compile                      # compile .po -> .mo (required for changes to show up)
```

CI (`.github/workflows/docker-build.yml`) runs pre-commit, pytest, and builds/pushes a Docker image on every push.

## Architecture

Two entrypoints share the `lib/` package:

- **`app.py` + `pages/*.py`** — Streamlit multi-page UI (dashboards, semantic search, RAG Q&A via Gemini). Every page must call `menu()` from `lib/menu.py` first; it sets the language and renders the sidebar nav (page registration happens there, not in `.streamlit/`).
- **`runner.py`** — Click CLI worker. Each iteration: update user list from Nextcloud → fetch changed Collectives pages → parse groups/protocols → run periodic tasks (avatar fetching, mailinglist distribution, calendar/deck reminders). Deployed as a separate container (`datafetcher` in compose) from the UI (`analytics`).

### Storage

- **CouchDB** (via `pycouchdb`) is the primary store. `lib/nextcloud/models/base.py` defines `CouchDBModel`: Pydantic models persisted as documents with a `type` field set to the class name as discriminator. Queries go through Mango `_find` selectors (`get_all`, `get_by`); required Mango indexes are created at startup in `lib/couchdb.py`, which also installs a JavaScript map/reduce view `_design/mentions` counting `mention://user/<name>` occurrences in page content — `pages/mentions.py` and `pages/groups.py` query that view directly. `calendar_notifier.py` and `deck_reminder.py` bypass the model layer and store processed-item state as raw docs.
- **ChromaDB** holds embeddings in a single collection (`lib/chromadb.py`); embedding function is Gemini or a HuggingFace server depending on settings, or disabled if neither is configured. `CollectivePage.save()` chunks content (langchain text splitter) and upserts embeddings; `Decision.save()` embeds one document. Deletes must cascade to ChromaDB — keep `save()`/`delete()` overrides in sync.

### Configuration — two layers

1. **Env vars** → `lib/settings.py` (pydantic-settings, nested delimiter `__`, e.g. `NEXTCLOUD__BASE_URL`). Infrastructure: URLs, credentials, Sentry, Gemini key.
2. **Runtime bot config lives in Nextcloud itself**: `BotConfig.load_config()` (`lib/nextcloud/config.py`) fetches a Collectives page (`configuration_page_id`) and parses a YAML block out of it. This holds all the keyword lists that drive markdown parsing (group prefixes, protocol/decision/moderation keywords), notification channel mappings, cooldowns, quiet hours. `config.example.yml` documents the shape. Parsing behavior changes are usually config-keyword changes, not code changes.

### Parsing pipeline

`collectives_loader.py` fetches page metadata via the Nextcloud OCS Collectives API and raw markdown via WebDAV (admin Basic auth), storing `CollectivePage` docs. `collectives_parser.py` classifies pages by title/path into `Group` or `Protocol` subtypes; the models' `update_from_page()` methods do the actual keyword-driven markdown parsing. Protocols extract decisions from `::: success ... :::` blocks into `Decision` docs (deleting a protocol/page cascades to its decisions). User mentions everywhere use `user_regex` from `lib/settings.py` (`mention://user/<name>`).

### i18n

User-facing strings must be wrapped with `_()` (or `_n()`) imported from `lib.settings`. `set_language()` swaps the gettext catalog at runtime; the UI picks language from the browser, the runner uses `settings.default_language`. After adding/changing strings run `make update_po`, translate in `locales/de/LC_MESSAGES/messages.po`, then `make compile`.

## Tests

`tests/conftest.py` injects `MagicMock` modules for `chromadb` and `google.genai`/`google.generativeai` into `sys.modules` **before** any `lib` import — that's what lets model code import without network access. Tests mock `bot_config` and patch `CouchDBModel.save`/`delete`; no live CouchDB is used. Coverage focuses on markdown parsing (`Group.update_from_page`, `Protocol.extract_decisions`) and the calendar notifier; loaders, mail, and pages are untested.
