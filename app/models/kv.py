from typing import Any

import edgy

from app.models.base import BaseDBModel


class KVState(BaseDBModel):
    """Small key-value store for service state (processed events/cards).

    Replaces the raw CouchDB documents the calendar notifier and deck
    reminder used to store outside the model layer.
    """

    key: str = edgy.CharField(max_length=255, unique=True)
    value: dict = edgy.JSONField(default=dict)

    natural_key_fields = ("key",)

    class Meta:
        tablename = "kv_state"


def get_state(key: str, default: Any = None) -> Any:
    entry = KVState.fetch_one(key=key)
    if entry is None:
        return default
    return entry.value


def set_state(key: str, value: Any) -> None:
    entry = KVState.fetch_one(key=key) or KVState(key=key)
    entry.value = value
    entry.store()
