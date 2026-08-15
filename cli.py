"""Maintenance CLI for one-off operations.

The recurring sync loop runs inside the web app (see `app/worker.py`);
this CLI covers manual syncs, cleanups and imports.
"""

import logging

import click

from app.db import init_db
from app.settings import set_language, settings, setup_locale

logger = logging.getLogger(__name__)


@click.group()
def cli() -> None:
    set_language(settings.default_language)
    setup_locale()
    init_db()


@cli.command()
@click.option(
    "--update-all", is_flag=True, default=False, help="Update and re-parse all pages"
)
@click.option(
    "--update-pages",
    default="",
    help="Comma-separated list of collectives page ids to fetch and force-update from Nextcloud",
)
def sync(update_all: bool, update_pages: str) -> None:
    """Run one sync/notify iteration (like a single worker cycle)."""
    from app.services.mail_fetcher import MailFetcher
    from app.worker import run_iteration

    run_iteration(MailFetcher(), update_all=update_all, update_pages=update_pages)


@cli.command()
def clear_parsed_data() -> None:
    """Delete all parsed groups, protocols, and decisions."""
    from app.worker import delete_all_parsed_data

    logger.info("Clearing all parsed data...")
    delete_all_parsed_data()


@cli.command()
def sync_matrix() -> None:
    """Create the Matrix chat rooms of all groups and invite their members.

    The regular sync already does this whenever a group page changes; this
    command walks every stored group at once (useful right after enabling
    the feature). Existing members are never removed.
    """
    from app.services.matrix_rooms import sync_all_groups

    count = sync_all_groups()
    click.echo(f"Synced Matrix rooms for {count} groups")


@cli.command()
@click.argument("xlsx_path", type=click.Path(exists=True))
def import_xlsx(xlsx_path: str) -> None:
    """Import logbook decisions from an XLSX file."""
    import pandas as pd

    from app.services.logbook_import import import_decisions_from_excel

    df = pd.read_excel(xlsx_path)
    errors = [msg for msg in import_decisions_from_excel(df) if msg]
    click.echo(f"Imported {len(df) - len(errors)}/{len(df)} decisions")
    for msg in errors:
        click.echo(f"  {msg}", err=True)


@cli.command()
@click.argument("json_path", type=click.Path(exists=True))
def import_decisions(json_path: str) -> None:
    """Import logbook decisions from a CouchDB JSON export.

    Create the file in the old setup with
    scripts/export_decisions_couchdb.py. Importing is idempotent (upsert by
    page_id + title), and page-bound decisions are replaced — not
    duplicated — by a later `sync --update-all`.
    """
    import json

    from app.services.logbook_import import import_decisions_from_records

    with open(json_path, encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list):
        raise click.ClickException("Expected a JSON array of decision records")

    errors = [msg for msg in import_decisions_from_records(records) if msg]
    click.echo(f"Imported {len(records) - len(errors)}/{len(records)} decisions")
    for msg in errors:
        click.echo(f"  {msg}", err=True)


if __name__ == "__main__":
    cli()
