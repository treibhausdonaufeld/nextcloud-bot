"""Ravyn application: web UI, static files and the background worker."""

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from ravyn import Gateway, Ravyn, Request, StaticFilesConfig, get
from ravyn.core.config.template import TemplateConfig
from ravyn.responses import RedirectResponse

from app.controllers.dashboard import dashboard, search_results
from app.controllers.groups import group_detail, groups_graph, groups_page
from app.controllers.logbook import logbook_page
from app.controllers.mentions import (
    mention_page_detail,
    mention_user_detail,
    mentions_graph,
    mentions_page,
)
from app.controllers.protocol_view import (
    protocol_media,
    protocol_restore,
    protocol_view,
)
from app.controllers.protocols import protocols_page
from app.controllers.timeline import functions_page, milestones_page
from app.db import init_db
from app.settings import available_languages, set_language, settings, setup_locale
from app.worker import worker_loop

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent


@get("/health")
def health() -> dict:
    return {"status": "ok"}


@get("/lang", status_code=302)
def switch_language(
    request: Request, code: str = "", next: str = "/"
) -> RedirectResponse:
    # only allow local paths: scheme-relative URLs (//host, /\host) would
    # allow an open redirect
    if not next.startswith("/") or next.startswith("//") or "\\" in next:
        next = "/"
    response = RedirectResponse(url=next, status_code=302)
    if code in available_languages:
        response.set_cookie("lang", code, max_age=365 * 24 * 3600)
    return response


_worker_task: asyncio.Task | None = None


async def on_startup() -> None:
    global _worker_task

    set_language(settings.default_language)
    setup_locale()
    init_db()

    # The worker can be disabled (e.g. for local UI development against an
    # existing database) by setting WORKER_ENABLED=false.
    if os.environ.get("WORKER_ENABLED", "true").lower() not in ("false", "0", "no"):
        _worker_task = asyncio.get_running_loop().create_task(worker_loop())
        logger.info("Background worker started")
    else:
        logger.info("Background worker disabled via WORKER_ENABLED")


async def on_shutdown() -> None:
    if _worker_task is not None:
        _worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _worker_task


app = Ravyn(
    routes=[
        Gateway(handler=health),
        Gateway(handler=switch_language),
        Gateway(handler=dashboard),
        Gateway(handler=search_results),
        Gateway(handler=groups_page),
        Gateway(handler=groups_graph),
        Gateway(handler=group_detail),
        Gateway(handler=functions_page),
        Gateway(handler=milestones_page),
        Gateway(handler=protocols_page),
        Gateway(handler=protocol_view),
        Gateway(handler=protocol_restore),
        Gateway(handler=protocol_media),
        Gateway(handler=logbook_page),
        Gateway(handler=mentions_page),
        Gateway(handler=mentions_graph),
        Gateway(handler=mention_user_detail),
        Gateway(handler=mention_page_detail),
    ],
    template_config=TemplateConfig(directory=BASE_DIR / "templates"),
    static_files_config=StaticFilesConfig(
        path="/static", directory=BASE_DIR / "static"
    ),
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
    enable_openapi=False,
)
