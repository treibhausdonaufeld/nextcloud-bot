# Nextcloud Bot

Various nextcloud automations.

All configured through collectives page in Nextcloud.

Features:

- Calendar appointment reminder
- Mailinglist
- Membership overview, who contributes where
- Deck reminder
- Protocol summaries
- Logbook overview

TODOs:

- bot checks for protocols:
  - bot check that title starts with date
  - check that users are mentioned
  - review of logbook summary and decision extractions!
- cleanup of deleted pages -> cleanup decisions, protocols, groups, everything related to this page!!
- test renaming of pages?!
- notification about parse-errors of bot-config to channel!

## Update translations

`make update_po`

## Upgrade packages

`uv sync -U`
