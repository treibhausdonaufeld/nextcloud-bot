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
with the same members. Entries may be written as links —
`[AG Struktur](https://chat.example.at/channel/AG-Struktur)` — in which case
only the plain name is used, so the markdown never leaks into the room title;
an entry that is nothing but a link is ignored.

Rooms and invitations are checked whenever the group's wiki page changes; the
bot only ever adds people — anyone who joined, was invited, or left again is
left alone, so nobody is removed or pestered with a second invitation.

The rooms are public, listed in this homeserver's room directory (so every
local user can find them by searching) and created with `m.federate: false`,
which keeps them on this server. Every sync re-checks the directory listing
and restores it if a room was unlisted, but federation is fixed when a room
is created and cannot be changed afterwards — rooms created before this
existed stay federated. If the homeserver refuses the listing, the sync logs
a warning and carries on inviting.

#### Letting the bot publish rooms (Synapse)

Since Synapse 1.126 the default is that **nobody except server admins may
publish a room to the directory**. The rooms are then created and joinable
but never show up in the directory search, and the bot logs
`Could not publish … (403 M_FORBIDDEN)`. Allow it in `homeserver.yaml`:

```yaml
# every local user may publish (the pre-1.126 behaviour)
room_list_publication_rules:
  - action: allow

# ...or only the bot:
room_list_publication_rules:
  - user_id: '@nextcloud-bot:example.com'
    action: allow
  - action: deny
```

Rules are matched in order, first match wins, and anything unmatched is
denied — so a list without a catch-all denies everyone else. Restart Synapse
(this is not hot-reloaded) and run `cli.py sync-matrix`: the sync re-checks
every room's directory entry and publishes the ones that are missing, so
rooms created while publication was denied are fixed without recreating
them. `enable_room_list_search` must stay `true` as well, otherwise the
directory search returns nothing regardless of what is published.

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
no per-channel configuration is needed. Apprise targets from the bot-config
page win when the channel has any; otherwise the message goes to the
channel's Matrix room and to the Rocket.Chat webhook.

While a homeserver _and_ a Rocket.Chat webhook are configured, every message
that reaches this stage — i.e. everything except the channels with Apprise
targets above — is sent to **both** systems, so a migration can run with the
old chat still live and nobody misses a reminder while people move over one
by one:

```bash
NOTIFY_DUAL_SEND=false   # back to "Matrix first, Rocket.Chat as fallback"
```

With the dual send switched off (or only one of the two configured),
Rocket.Chat receives only what Matrix could not deliver — a channel with no
room, an API error, or a DM to somebody without a Matrix account. Note that
a message is never sent to Rocket.Chat twice: an undeliverable Matrix
message still produces exactly one webhook post.

Direct messages (`@user` channels, used for protocol feedback) are delivered
as Matrix DMs: the bot reuses the one-to-one room recorded in its `m.direct`
account data — including a DM the recipient opened themselves — and creates
one if there is none. A DM room the recipient has left is replaced rather
than reused. Unlike channel rooms, which the bot never creates from a
notification, a DM room has to be created on demand.

To try a deployment out without messaging the whole association, redirect
every notification — channel messages and DMs alike, whichever backend they
would go out through:

```bash
NOTIFY_CHANNEL_OVERWRITE=@max.mueller   # everything as a DM to one person
NOTIFY_CHANNEL_OVERWRITE=bot-test       # everything into one channel
```

It takes precedence over the bot-config page's `notifier.channel_overwrite`,
so it can be set and removed without editing the wiki. Note that Apprise
targets still win over Matrix, so a global `notifier.default_urls` entry
keeps its precedence for the redirected channel too.

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
