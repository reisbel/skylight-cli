"""A thin client over the Skylight endpoints this tool needs.

Scope is deliberately narrow: frames, family members, chores and lists. The
private API exposes far more (meals, rewards, messages, the task box), but those
sit behind a Skylight Plus subscription and cannot be exercised without one, so
they are left out rather than shipped untested.

Responses are JSON:API shaped - ``{"data": [{"id", "type", "attributes"}]}`` -
and :func:`flatten` folds each record down to a plain dict with ``id`` included.
"""

from __future__ import annotations

from typing import Any

import httpx

from .auth import Credentials
from .constants import API_PREFIX, BASE_URL, DEFAULT_TIMEOUT, USER_AGENT
from .errors import (
    SkylightAPIError,
    SkylightAuthError,
    SkylightError,
    SkylightNotFoundError,
)


def index_included(payload: Any) -> dict[tuple[str, str], dict[str, Any]]:
    """Index a response's ``included`` side-loaded records by type and id."""
    included = payload.get("included") if isinstance(payload, dict) else None
    if not isinstance(included, list):
        return {}
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in included:
        if isinstance(record, dict) and record.get("id") is not None:
            index[(str(record.get("type")), str(record["id"]))] = record.get("attributes") or {}
    return index


def flatten(
    record: Any, included: dict[tuple[str, str], dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Fold a JSON:API record into a flat dict.

    Attributes come up to the top level, and each to-one relationship
    contributes a ``<name>_id``. When the response side-loaded the related
    record, its human label lands under ``<name>`` as well, which is what turns
    an opaque category id into "Lucas".
    """
    if not isinstance(record, dict):
        return {}
    attributes = record.get("attributes")
    if not isinstance(attributes, dict):
        return dict(record)

    flat: dict[str, Any] = {"id": record.get("id"), "type": record.get("type"), **attributes}

    relationships = record.get("relationships")
    if isinstance(relationships, dict):
        for name, relationship in relationships.items():
            data = (relationship or {}).get("data")
            if not isinstance(data, dict) or data.get("id") is None:
                continue
            flat[f"{name}_id"] = data["id"]
            related = (included or {}).get((str(data.get("type")), str(data["id"])))
            if related:
                label = related.get("label") or related.get("name")
                if label:
                    flat[name] = label
    return flat


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class SkylightClient:
    """Talks to ``app.ourskylight.com`` as an authenticated user."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        http: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.credentials = credentials
        self._http = http or httpx.Client(timeout=timeout)
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> SkylightClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- plumbing ---------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(
                method,
                BASE_URL + path,
                headers={
                    "Authorization": self.credentials.authorization,
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise SkylightError(f"Network error on {method} {path}: {exc}") from exc

        self._raise_for_status(response, method, path)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _raise_for_status(response: httpx.Response, method: str, path: str) -> None:
        if response.is_success:
            return
        status = response.status_code
        if status == 401:
            raise SkylightAuthError("Session expired or invalid. Run `skylight login` again.")
        if status == 403:
            raise SkylightAPIError(
                f"{method} {path} was forbidden (HTTP 403). This usually means the "
                "feature needs an active Skylight Plus subscription.",
                status_code=status,
            )
        if status == 404:
            raise SkylightNotFoundError(f"{method} {path} not found (HTTP 404)")
        raise SkylightAPIError(
            f"{method} {path} failed with HTTP {status}",
            status_code=status,
            body=response.text[:500] or None,
        )

    def _records(self, payload: Any) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            return []
        included = index_included(payload)
        return [flatten(record, included) for record in data]

    # -- frames and family members ----------------------------------------

    def frames(self) -> list[dict[str, Any]]:
        """Every household (Skylight calls them frames) on this account."""
        return self._records(self._request("GET", f"{API_PREFIX}/frames"))

    def resolve_frame_id(self, wanted: str | None = None) -> str:
        """Return a frame id, picking the only frame when there is just one."""
        if wanted:
            return str(wanted)
        frames = self.frames()
        if not frames:
            raise SkylightNotFoundError("This Skylight account has no frames")
        if len(frames) > 1:
            names = ", ".join(f"{f.get('name') or '?'} ({f.get('id')})" for f in frames)
            raise SkylightError(
                f"This account has several frames, so one must be chosen: {names}. "
                "Pass --frame or set SKYLIGHT_FRAME_ID."
            )
        return str(frames[0]["id"])

    def members(self, frame_id: str) -> list[dict[str, Any]]:
        """Family member profiles, which Skylight models as categories."""
        return self._records(self._request("GET", f"{API_PREFIX}/frames/{frame_id}/categories"))

    def resolve_member_id(self, frame_id: str, name: str) -> str:
        """Look up a family member by name, case-insensitively."""
        members = self.members(frame_id)
        wanted = name.strip().casefold()
        for member in members:
            label = str(member.get("label") or member.get("name") or "")
            if label.casefold() == wanted or str(member.get("id")) == name:
                return str(member["id"])
        known = ", ".join(str(m.get("label") or m.get("name") or m.get("id")) for m in members)
        raise SkylightNotFoundError(f"No family member named {name!r}. Known: {known}")

    # -- chores ------------------------------------------------------------

    def chores(
        self,
        frame_id: str,
        *,
        after: str | None = None,
        before: str | None = None,
        include_late: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Chores in a date range.

        The endpoint rejects an unbounded query with a 422, so a range is
        required rather than optional.
        """
        params = _without_none(
            {
                "after": after,
                "before": before,
                "include_late": "true" if include_late else None,
            }
        )
        return self._records(
            self._request("GET", f"{API_PREFIX}/frames/{frame_id}/chores", params=params or None)
        )

    def add_chore(
        self,
        frame_id: str,
        summary: str,
        *,
        start: str | None = None,
        start_time: str | None = None,
        category_id: str | None = None,
        reward_points: int | None = None,
        recurrence_set: list[str] | None = None,
        recurring_until: str | None = None,
        emoji_icon: str | None = None,
    ) -> dict[str, Any]:
        body = _without_none(
            {
                "summary": summary,
                "start": start,
                "start_time": start_time,
                "category_id": category_id,
                "reward_points": reward_points,
                "recurring": True if recurrence_set else None,
                "recurrence_set": recurrence_set,
                "recurring_until": recurring_until,
                "emoji_icon": emoji_icon,
            }
        )
        payload = self._request("POST", f"{API_PREFIX}/frames/{frame_id}/chores", json=body)
        return flatten((payload or {}).get("data", payload))

    def complete_chore(
        self,
        frame_id: str,
        chore_id: str,
        *,
        instance_date: str | None = None,
        category_id: str | None = None,
        status: str = "complete",
    ) -> Any:
        """Mark a chore done.

        Recurring chores are series, so a completion targets one instance by
        date rather than the chore itself.
        """
        body = _without_none(
            {
                "status": status,
                "instance_date": instance_date,
                "category_id": category_id,
            }
        )
        return self._request(
            "PUT", f"{API_PREFIX}/frames/{frame_id}/chores/{chore_id}/completions", json=body
        )

    def delete_chore(self, frame_id: str, chore_id: str, *, apply_to: str | None = None) -> None:
        params = {"apply_to": apply_to} if apply_to else None
        self._request("DELETE", f"{API_PREFIX}/frames/{frame_id}/chores/{chore_id}", params=params)

    # -- lists -------------------------------------------------------------

    def lists(self, frame_id: str) -> list[dict[str, Any]]:
        return self._records(self._request("GET", f"{API_PREFIX}/frames/{frame_id}/lists"))

    def resolve_list_id(self, frame_id: str, name: str) -> str:
        """Look up a list by name, case-insensitively, or accept a raw id."""
        available = self.lists(frame_id)
        wanted = name.strip().casefold()
        for entry in available:
            label = str(entry.get("label") or entry.get("name") or "")
            if label.casefold() == wanted or str(entry.get("id")) == name:
                return str(entry["id"])
        known = ", ".join(str(e.get("label") or e.get("name") or e.get("id")) for e in available)
        raise SkylightNotFoundError(f"No list named {name!r}. Known: {known}")

    def list_items(self, frame_id: str, list_id: str) -> list[dict[str, Any]]:
        return self._records(
            self._request("GET", f"{API_PREFIX}/frames/{frame_id}/lists/{list_id}/list_items")
        )

    def add_list_item(
        self, frame_id: str, list_id: str, label: str, *, section: str | None = None
    ) -> dict[str, Any]:
        body = _without_none({"label": label, "section": section})
        payload = self._request(
            "POST", f"{API_PREFIX}/frames/{frame_id}/lists/{list_id}/list_items", json=body
        )
        return flatten((payload or {}).get("data", payload))

    def update_list_item(
        self,
        frame_id: str,
        list_id: str,
        item_id: str,
        *,
        label: str | None = None,
        status: str | None = None,
    ) -> Any:
        body = _without_none({"label": label, "status": status})
        return self._request(
            "PUT",
            f"{API_PREFIX}/frames/{frame_id}/lists/{list_id}/list_items/{item_id}",
            json=body,
        )

    def complete_list_item(
        self, frame_id: str, list_id: str, item_id: str, *, done: bool = True
    ) -> Any:
        return self.update_list_item(
            frame_id, list_id, item_id, status="completed" if done else "pending"
        )

    def delete_list_item(self, frame_id: str, list_id: str, item_id: str) -> None:
        self._request(
            "DELETE", f"{API_PREFIX}/frames/{frame_id}/lists/{list_id}/list_items/{item_id}"
        )
