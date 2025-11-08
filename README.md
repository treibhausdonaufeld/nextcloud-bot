# Nextcloud Bot

Various nextcloud automations.

All configured through collectives page in Nextcloud.

Features:

- Calendar appointment reminder
- Mailinglist
- Membership overview, who contributes where
- Deck reminder
- Protocol schedules
- Protocol summaries
- Logbook overview

TODOs:

- (maybe) bot checks - confirmation of protocol summary!
- protocol statistics (moderation, protocol) schedules/assignments
- member contribution statistics
- test renaming of pages?!
- notification about parse-errors of bot-config to channel!

## Run tests

- `uv run pytest`
- with coverage: `uv run pytest --cov=lib --cov-report=html --cov-report=term`

## Update translations

- `make update_po`
- `make compile`

## Upgrade packages

`uv sync -U`
