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

TODOs:

- protocol statistics (moderation, protocol) schedules/assignments
- moving pages to Archive after e.g. 12 months?
- notification about parse-errors of bot-config to channel!
- temporal suspension of members ("Karenz"), not sure yet how to implement...

## Run tests

- `uv run pytest`
- with coverage: `uv run pytest --cov=lib --cov-report=html --cov-report=term`

## Update translations

- `make update_po`
- `make compile`

## Upgrade packages

`uv sync -U`
