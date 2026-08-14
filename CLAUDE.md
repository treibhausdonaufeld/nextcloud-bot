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
uv run python cli.py sync-matrix        # create/refresh all group chat rooms
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

- **Controllers** (`app/controllers/`) are synchronous handlers (Ravyn runs them in a threadpool): dashboard (FTS search), groups (vis-network org chart), timeline (plotly from markdown tables on a Collectives page), protocols, logbook (decision cards + XLSX import), members (role overview + role history), mentions (table + network graph). Graph pages fetch JSON endpoints (`/groups/graph.json`, `/mentions/graph.json`) and load click-detail partials via htmx.
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

1. **Env vars** → `app/settings.py` (pydantic-settings, nested delimiter `__`, e.g. `NEXTCLOUD__BASE_URL`). Infrastructure: URLs, credentials, Sentry, `DATABASE_URL`. `AUTH__MEMBER_GROUP_NAME` (default `Mitglieder`) names the authentik group whose members are listed on `/members` and offered in the user pickers; `""` shows every user.
2. **Runtime bot config lives in Nextcloud itself**: `BotConfig.load_config()` (`app/services/config.py`) fetches a Collectives page (`configuration_page_id`) and parses a YAML block out of it. This holds all the keyword lists that drive markdown parsing (group prefixes, protocol/decision/moderation keywords), notification channel mappings, cooldowns, quiet hours. `config.example.yml` documents the shape. Parsing behavior changes are usually config-keyword changes, not code changes.

### Parsing pipeline

`app/services/collectives_loader.py` fetches page metadata via the Nextcloud OCS Collectives API and raw markdown via WebDAV (admin Basic auth), upserting `CollectivePage` rows. `collectives_parser.py` classifies pages by title/path into group/protocol subtypes; the models' `update_from_page()` methods do the actual keyword-driven markdown parsing. Protocols extract decisions from `::: success ... :::` blocks into `Decision` rows (deleting a protocol/page cascades to its decisions). User mentions everywhere use `user_regex` from `app/settings.py` (`mention://user/<name>`).

### Matrix chat rooms

`app/services/matrix.py` is a thin wrapper around the Matrix Client-Server API (resolve alias, create room, read members, invite, join) using the admin token from `settings.matrix`; `app/services/matrix_rooms.py` turns a `Group` into channels and syncs them. Each group gets one public room named after the group (`"AG Struktur"` → `#ag-struktur:<server_name>`, see `channel_slug()` for the German transliteration), plus one per name listed after the `Chat-Kanäle:` keyword on the page (`organisation.group_chat_channel_keywords`, parsed into `Group.chat_channels`). `sync_group_rooms()` runs after `GroupRole.sync_group()` in `parse_groups`, so rooms and memberships are reconciled whenever a group page changes; it swallows its own errors so a chat outage never breaks parsing. Invited user ids come from the authentik username (`NCUserList.chat_username`) — the same handle Rocket.Chat uses. The sync only ever **adds**: a user with any existing membership event (join/invite/leave/ban) is skipped, so leaving a room is not undone and nobody gets invited twice. `MATRIX__DEFAULT_ROOMS` (comma-separated, parsed with `NoDecode` so the env var does not have to be JSON) names rooms every member belongs to regardless of any group; since they are not tied to a wiki page, `sync_default_rooms()` runs once per worker iteration from `run_periodic_tasks` instead, and invites `NCUserList.get_member_users()`. Everything is inert unless `MATRIX__HOMESERVER_URL` and `MATRIX__ADMIN_TOKEN` are set (`settings.matrix.enabled`); `cli.py sync-matrix` walks the default rooms plus all stored groups for the initial rollout.

Notifications reuse those rooms: `app/services/matrix_notify.py::send_matrix_message()` turns a logical channel name into the same alias slug and posts an `m.text` message (markdown rendered to `formatted_body`, sanitized with nh3) into the room if it already exists — it never creates a channel room. `@user` channels go out as DMs instead: `direct_room()` reuses the room recorded in the bot's `m.direct` account data (skipping rooms the recipient left) and creates one via `create_dm_room()` otherwise, which is the one case where a notification does create a room. `app/services/notify.py` tries Apprise targets first, then Matrix, then the Rocket.Chat webhook. `notify.target_channel()` applies the testing overrides — `NOTIFY_CHANNEL_OVERWRITE` (env, wins) over the bot-config `notifier.channel_overwrite` — and `send_rocketchat_message` honours the env one as well, so no path escapes the redirect. `Notifier.check_event` (calendar) falls back to `Group.find_in_text(summary)` → `channel_slug(group.name)` when no `channel_keywords` entry matches, so "AG Struktur Treffen" lands in `ag-struktur`; `calendar_notifier.group_channel_fallback: false` disables that.

### Role history

`Group` rows only hold the _current_ membership of a group page, so every observed membership is additionally recorded in the `group_roles` table (`app/models/group_role.py`) as a row with `role` (coordination/delegate/member), `start_date` and — once the role ends — `end_date`; open rows (`end_date is None`) are the roles held right now. `GroupRole.sync_group()` runs after `Group.update_from_page()` in `parse_groups` and closes/opens rows by comparing the parsed group against the stored ones, dating changes with the group page's Nextcloud timestamp rather than the sync time. It is idempotent: a role that reappears after being closed at the same (or a later) timestamp is reopened instead of duplicated, so re-parsing all pages does not fragment the history. `backfill_role_history()` seeds groups that were parsed before the table existed (skipped after the first run), and deleting a `Group` closes its open rows without dropping the history. `/members` (`app/controllers/members.py`) renders the current roles from the `Group` rows (the source of truth) and merges the start dates plus the past periods from `group_roles`; the member and role detail partials are swapped into a dialog via htmx.

Groups are retired by `remove_stale_groups()` (`app/services/collectives_parser.py`), which runs over all stored groups after the per-page parsing: a group whose `CollectivePage` row is gone (deleted in Nextcloud, see `delete_orphaned_pages`) or whose page sits below an archive page (`organisation.archive_page_names`, default `archiv`/`archive` — matched per path segment, so `Archiv 2024` counts but `Archivierung` does not) gets deleted, which closes its open roles via `Group.before_remove()` and keeps the history. Because archiving a page archives its subpages, subgroups retire with their parent; the sweep walks every group instead of only the changed pages because moving a page does not touch its subpages' timestamps. For the same reason `store_pages` treats a changed path or title as a change (`_moved()`), otherwise subpages would keep their pre-move path. Retirement is dated by the sync that noticed it, not by the page timestamp, since moving or deleting a page in Nextcloud leaves the modification time untouched.

Who counts as a member comes from authentik: `update_from_authentik()` stores each user's group names in `NCUser.authentik_groups`, and `NCUserList.is_member()` checks them against `settings.auth.member_group_name`. The overview and the group page's user picker list members only; the role history dialogs and the org chart still show everyone a group page names, so past roles of people who left the association stay visible. The filter switches itself off when the setting is empty, when authentik is not connected, or while no user has group data yet (i.e. before the first sync after the upgrade) — otherwise those cases would render an empty member list.

### Protocol versioning & media

Protocol pages are versioned self-contained in the bot database: on every content change during sync, `ProtocolVersion.record()` stores a full markdown snapshot, a unified diff to the previous version and the editing user (Nextcloud `lastUserId`); `app/services/protocol_media.py` copies referenced `.attachments.<id>/` files to the media folder (env var `MEDIA_FOLDER`, default `/data/media`) laid out as `YYYY/MM/DD/<page-id>/attachments/<folder-id>/<name>` (date = protocol date) so old attachments can be pruned by date; `ProtocolMedia` rows hold the metadata, and a pruned file just 404s without being re-downloaded. Both are hooked into `store_pages` and (for backfill) `parse_protocols` and are idempotent. `/protocols/{page_id}/view` (optionally `?version=N`) serves the protocol from `app/controllers/protocol_view.py`: an htmx request (opened from the protocols page or logbook via `hx-get`/`hx-push-url`) gets the popup partial (`partials/protocol_view.html`) swapped into a dialog; any other request (a bookmarked or shared link, opened fresh) gets the same content rendered as a full standalone page (`protocol_page.html`) — both include the shared `partials/protocol_view_body.html`, so every protocol/version is directly linkable. It renders the markdown (`app/services/protocol_render.py`: `::: x :::` callouts, `mention://` links, media links rewritten to `/protocols/{page_id}/media/{folder-id}/{name}`, output sanitized with nh3), lists the version history with per-version diffs and shows each version's raw markdown for manual restoring — the app itself never writes back to Nextcloud. `delete_orphaned_pages` never deletes protocol pages older than 7 days (`PROTOCOL_DELETE_PROTECTION_DAYS`), so protocols that vanish from Nextcloud keep their history here; renames are unaffected since the page_id stays stable.

### i18n

User-facing strings in Python must be wrapped with `_()` (or `_n()`) from `app.settings`; templates get `_`/`_n` passed in explicitly via `app/i18n.py::template_context` (the request language comes from the `lang` cookie or Accept-Language). The gettext catalog is stored in a ContextVar, so per-request switching is thread-safe; `locale.setlocale` is process-global and only set once at startup. After adding/changing strings run `make update_po`, translate in `locales/de/LC_MESSAGES/messages.po`, then `make compile`.

## Tests

Tests run without a database: they mock `bot_config` and patch `store`/`remove`/`fetch` on the models, so the Edgy registry never connects. `tests/conftest.py` resets the class-level caches (`Group._cached_groups`, `NCUserList._cached_users`) between tests. Coverage focuses on markdown parsing (`Group.update_from_page`, `Protocol.extract_decisions`) and the calendar notifier; loaders, mail, and controllers are untested.
