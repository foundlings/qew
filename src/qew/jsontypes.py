"""Shapes shared across modules: a JSON object whose fields are checked where they are read."""

from typing import Any

type JsonObject = dict[str, Any]
