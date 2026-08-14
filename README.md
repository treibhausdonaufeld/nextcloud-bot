# Nextcloud Bot

Various nextcloud automations.

All configured through collectives page in Nextcloud.

Features:

- Calendar appointment reminder
- Mailinglist
- Groups overview and members
- Deck reminder
- Protocol summaries
- Logbook
- Matrix chat rooms per group

A single [Ravyn](https://www.ravyn.dev/) application serves the web UI
(Jinja2 + htmx + Pico.css) and runs the background sync/notification worker
in-process. Data is stored in a SQLite file ([Edgy](https://edgy.dymmond.com/)
ORM), with full-text search via SQLite FTS5.

TODOs:

- protocol statistics (moderation, protocol) schedules/assignments
- moving pages to Archive after e.g. 12 months?
- notification about parse-errors of bot-config to channel!
- temporal suspension of members ("Karenz"), not sure yet how to implement...

## Run locally

- `uv run uvicorn app.main:app --reload` — web UI + worker on :8000
- `uv run python cli.py sync` — one manual sync/notify iteration
- `uv run python cli.py sync --update-all` — re-parse all pages
- `uv run python cli.py clear-parsed-data`
- `uv run python cli.py sync-matrix` — create/refresh the Matrix rooms of all groups
- `uv run python cli.py import-xlsx decisions.xlsx`

Set `WORKER_ENABLED=false` to run the UI without the background worker.

## Matrix chat rooms

Every active group gets a public Matrix room named after it — "AG Struktur"
becomes `#ag-struktur:example.com` — and everyone the group page lists
(coordination, delegates, members) is invited to it. Writing

```markdown
**Chat-Kanäle:** Fragen an AG Struktur, Termine
```

on the group page creates `#fragen-an-ag-struktur` and `#termine` next to it,
with the same members. Rooms and invitations are checked whenever the group's
wiki page changes; the bot only ever adds people — anyone who joined, was
invited, or left again is left alone, so nobody is removed or pestered with a
second invitation.

Rooms that everybody belongs to — independent of any group — are configured
as a comma-separated list. `MATRIX__DEFAULT_ROOMS="Allgemein, Ankündigungen"`
creates `#allgemein` and `#ankuendigungen` and invites every member to them.
These are reconciled once per worker iteration (not per page change), which
is what picks up newly joined members. "Member" here means the same set the
`/members` page shows: everyone in `AUTH__MEMBER_GROUP_NAME`, i.e. every
enabled user when that setting is empty.

The feature is off until a homeserver and an access token are configured:

```bash
MATRIX__HOMESERVER_URL=https://matrix.example.com
MATRIX__ADMIN_TOKEN=syt_...            # may create rooms and invite users
MATRIX__SERVER_NAME=example.com        # alias domain, defaults to the URL host
MATRIX__USER_DOMAIN=example.com        # user id domain, defaults to SERVER_NAME
MATRIX__ROOM_PREFIX=                   # optional alias prefix, e.g. "thd-"
MATRIX__DEFAULT_ROOMS=                 # rooms every member is invited to
```

Invited user ids are built from the authentik username (the same handle used
for chat DMs): `@fabian.helm:example.com`. Run
`uv run python cli.py sync-matrix` once after enabling the feature to create
the rooms of all existing groups at once.

### Notifications into Matrix

Bot notifications are addressed by a logical channel name, and that name maps
onto a room alias with the same slug rule (`ug-it` → `#ug-it:example.com`), so
no per-channel configuration is needed. The order is: Apprise targets from the
bot-config page if the channel has any, otherwise the channel's Matrix room if
it exists, otherwise the Rocket.Chat webhook. Direct messages (`@user`
channels, used for protocol feedback) always take the Rocket.Chat path — the
bot does not open Matrix DMs.

Calendar reminders pick their channel in two steps: the `channel_keywords`
mapping on the bot-config page wins, and when no keyword matches, the event is
announced in the channel of the group named in its title — "AG Struktur
Treffen" goes to `ag-struktur`, matching group names and short names on word
boundaries and preferring the most specific one. Set
`calendar_notifier.group_channel_fallback: false` to keep the old behaviour of
only notifying the mapped channels.

## Migrating from the old CouchDB setup

Nextcloud is the source of truth for pages, groups and protocols — after
deploying, `python cli.py sync --update-all` rebuilds everything. Only
logbook decisions need to be carried over (manually imported ones are not
reproducible from Nextcloud):

1. In the **old** setup, export all decisions (stdlib-only script, can be
   copied into the old container):

   ```bash
   python3 scripts/export_decisions_couchdb.py \
       --url http://admin:password@localhost:5984/ \
       --base-url https://your.nextcloud.example \
       --output decisions_export.json
   ```

2. In the **new** setup, import the file:

   ```bash
   python cli.py import-decisions decisions_export.json
   ```

Both the import and a later `sync --update-all` are duplicate-safe: decisions
are upserted by their `page_id` + title key, and re-parsing a protocol
replaces its page's decisions instead of adding new ones.

## Run tests

- `uv run pytest`
- with coverage: `uv run pytest --cov=app --cov-report=html --cov-report=term`

## Update translations

- `make update_po`
- `make compile`

## Upgrade packages

`uv sync -U`
