"""Notebook allowlist gate for the MCP server.

Restricts which notebooks are visible/usable through the MCP, driven by
``NOTEBOOKLM_MCP_NOTEBOOK_ALLOWLIST`` (comma-separated notebook UUIDs).

When the env var is unset or empty, no restriction is applied (default
behaviour). When set, every tool call carrying a ``notebook_id`` or
``notebook_ids`` kwarg is gated, and ``notebook_list`` results are filtered.
"""

from __future__ import annotations

import os
from typing import Any

_ENV_VAR = "NOTEBOOKLM_MCP_NOTEBOOK_ALLOWLIST"


def _load_allowlist() -> set[str] | None:
    raw = os.environ.get(_ENV_VAR, "").strip()
    if not raw:
        return None
    return {x.strip() for x in raw.split(",") if x.strip()}


def is_restricted() -> bool:
    return _load_allowlist() is not None


def _forbidden(notebook_id: Any) -> dict[str, Any]:
    return {
        "status": "error",
        "error": f"notebook not in allowlist: {notebook_id}",
        "hint": (
            "This MCP server is restricted to a curated set of notebooks. "
            "Use notebook_list to see what is available."
        ),
    }


def _normalise_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        return [x.strip() for x in s.split(",") if x.strip()]
    return [str(value)]


def check_kwargs(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Return a forbidden response dict if the call targets a disallowed
    notebook, otherwise None.
    """
    allow = _load_allowlist()
    if allow is None:
        return None

    nid = kwargs.get("notebook_id")
    if nid is not None and nid not in allow:
        return _forbidden(nid)

    ids = _normalise_ids(kwargs.get("notebook_ids"))
    denied = [x for x in ids if x not in allow]
    if denied:
        return _forbidden(",".join(denied))

    return None


def filter_notebook_list_result(result: Any) -> Any:
    """If ``result`` looks like a ``notebook_list`` response, prune notebooks
    outside the allowlist. No-op when unrestricted.
    """
    allow = _load_allowlist()
    if allow is None or not isinstance(result, dict):
        return result
    notebooks = result.get("notebooks")
    if not isinstance(notebooks, list):
        return result
    filtered = [
        n
        for n in notebooks
        if isinstance(n, dict)
        and (n.get("id") or n.get("notebook_id")) in allow
    ]
    new_result = dict(result)
    new_result["notebooks"] = filtered
    if "total" in new_result:
        new_result["total"] = len(filtered)
    if "count" in new_result:
        new_result["count"] = len(filtered)
    return new_result
