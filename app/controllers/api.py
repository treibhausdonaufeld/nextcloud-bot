"""Read-only API for other tools, plus its browsable documentation.

Both the endpoints and the docs use the normal web UI access: the whole
deployment sits behind the single-sign-on proxy, so no additional token is
required here. Only the machine-readable data lives below `/api/v1/`.
"""

from typing import Any

from pydantic import BaseModel
from ravyn import Request, Template, get
from ravyn.responses import JSONResponse

from app.i18n import activate, template_context
from app.services.external_api import (
    GroupsResponse,
    MembersResponse,
    groups_payload,
    members_payload,
)
from app.settings import _, settings


@get(
    "/api/v1/groups",
    summary="List all groups with their current members",
    tags=["External API"],
)
def api_groups(request: Request) -> JSONResponse:
    """Current groups with the role each member holds right now."""
    return JSONResponse(groups_payload(), headers={"Cache-Control": "no-store"})


@get(
    "/api/v1/members",
    summary="List all members with their current groups",
    tags=["External API"],
)
def api_members(request: Request) -> JSONResponse:
    """Current members with the groups and roles they hold right now."""
    return JSONResponse(members_payload(), headers={"Cache-Control": "no-store"})


def _model_schema(model: type[BaseModel], components: dict[str, Any]) -> dict[str, str]:
    """Register `model` (and nested models) in `components`, return a $ref."""
    schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    for name, definition in schema.pop("$defs", {}).items():
        components.setdefault(name, definition)
    components[model.__name__] = schema
    return {"$ref": f"#/components/schemas/{model.__name__}"}


def openapi_spec() -> dict[str, Any]:
    """The OpenAPI 3.1 document for the API endpoints."""
    components: dict[str, Any] = {}
    groups_schema = _model_schema(GroupsResponse, components)
    members_schema = _model_schema(MembersResponse, components)

    return {
        "openapi": "3.1.0",
        "info": {
            "title": _("%(name)s external API") % {"name": settings.name},
            "version": "1.0.0",
            "description": _(
                "Read-only access to the current group and member assignments. "
                "Access is controlled by the deployment's single sign-on; "
                "historical data (past roles and leaves) is not exposed."
            ),
        },
        "servers": [{"url": "/"}],
        "tags": [
            {
                "name": "External API",
                "description": _("Available to anyone who can access this deployment."),
            }
        ],
        "paths": {
            "/api/v1/groups": {
                "get": {
                    "tags": ["External API"],
                    "summary": _("List all groups with their current members"),
                    "description": _(
                        "Every active group with its parent group and the members "
                        "that currently hold a role in it. Retired groups are not "
                        "included."
                    ),
                    "operationId": "list_external_groups",
                    "responses": {
                        "200": {
                            "description": _("Current groups and their members"),
                            "content": {"application/json": {"schema": groups_schema}},
                        },
                    },
                }
            },
            "/api/v1/members": {
                "get": {
                    "tags": ["External API"],
                    "summary": _("List all members with their current groups"),
                    "description": _(
                        "Every current member with the groups and roles they hold "
                        "right now. Members without a current group are included "
                        "with an empty list."
                    ),
                    "operationId": "list_external_members",
                    "responses": {
                        "200": {
                            "description": _("Current members and their groups"),
                            "content": {"application/json": {"schema": members_schema}},
                        },
                    },
                }
            },
        },
        "components": {"schemas": components},
    }


@get("/api/openapi.json", tags=["External API"])
def api_openapi(request: Request) -> JSONResponse:
    """The OpenAPI document consumed by the browsable documentation."""
    activate(request)
    return JSONResponse(openapi_spec())


@get("/api/docs")
def api_docs(request: Request) -> Template:
    """Browsable documentation for the API."""
    activate(request)
    return Template(
        name="api_docs.html",
        context=template_context(
            request,
            spec=openapi_spec(),
            base_url=str(request.base_url).rstrip("/"),
        ),
    )
