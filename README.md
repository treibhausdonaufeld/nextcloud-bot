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
- `uv run python cli.py import-xlsx decisions.xlsx`

Set `WORKER_ENABLED=false` to run the UI without the background worker.

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
