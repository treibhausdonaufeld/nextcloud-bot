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


if __name__ == "__main__":
    cli()
