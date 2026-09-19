"""Client behaviour against mocked Skylight responses."""

from __future__ import annotations

import httpx
import pytest

from skylight_cli.auth import Credentials
from skylight_cli.client import (
    SkylightClient,
    flatten,
    index_included,
    split_chore_id,
)
from skylight_cli.errors import (
    SkylightAPIError,
    SkylightAuthError,
    SkylightError,
    SkylightNotFoundError,
)

FRAMES = {"data": [{"id": "77", "type": "frame", "attributes": {"name": "Kitchen"}}]}
MEMBERS = {
    "data": [
        {"id": "1", "type": "category", "attributes": {"label": "Lucas", "color": "blue"}},
        {"id": "2", "type": "category", "attributes": {"label": "Camila", "color": "pink"}},
    ]
}
LISTS = {
    "data": [
        {"id": "9", "type": "list", "attributes": {"label": "Groceries", "kind": "shopping"}},
        {"id": "10", "type": "list", "attributes": {"label": "To Do", "kind": "todo"}},
    ]
}


def build(handler) -> SkylightClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return SkylightClient(Credentials("tok-123"), http=http)


def route(mapping: dict[tuple[str, str], object], recorder: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(request)
        key = (request.method, request.url.path)
        if key not in mapping:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=mapping[key])

    return handler


CHORE_PAYLOAD = {
    "data": [
        {
            "id": "1073-2026-09-19-0600",
            "type": "chore",
            "attributes": {"summary": "Brush teeth", "start": "2026-09-19", "status": "pending"},
            "relationships": {
                "category": {"data": {"id": "11454457", "type": "category"}},
                "completed_category": {"data": None},
            },
        }
    ],
    "included": [
        {
            "id": "11454457",
            "type": "category",
            "attributes": {"label": "Lucas", "color": "#83B9E9"},
        }
    ],
}


def test_flatten_folds_attributes_up() -> None:
    assert flatten({"id": "5", "type": "chore", "attributes": {"summary": "Dishes"}}) == {
        "id": "5",
        "type": "chore",
        "summary": "Dishes",
    }
    assert flatten({"id": "5"}) == {"id": "5"}
    assert flatten("nonsense") == {}


def test_flatten_pulls_relationship_ids_up() -> None:
    flat = flatten(CHORE_PAYLOAD["data"][0])
    assert flat["category_id"] == "11454457"
    # An empty to-one relationship contributes nothing at all.
    assert "completed_category_id" not in flat
    # With no included index there is no label to attach.
    assert "category" not in flat


def test_flatten_resolves_labels_from_included() -> None:
    flat = flatten(CHORE_PAYLOAD["data"][0], index_included(CHORE_PAYLOAD))
    assert flat["category"] == "Lucas"
    assert flat["category_id"] == "11454457"


def test_index_included_tolerates_a_response_without_it() -> None:
    assert index_included({"data": []}) == {}
    assert index_included("nonsense") == {}


def test_chores_resolve_who_from_side_loaded_categories() -> None:
    with build(route({("GET", "/api/frames/77/chores"): CHORE_PAYLOAD})) as client:
        rows = client.chores("77", after="2026-09-19", before="2026-09-19")
    assert rows[0]["summary"] == "Brush teeth"
    assert rows[0]["category"] == "Lucas"


def test_chores_send_the_date_window() -> None:
    seen: list[httpx.Request] = []
    with build(route({("GET", "/api/frames/77/chores"): CHORE_PAYLOAD}, seen)) as client:
        client.chores("77", after="2026-09-14", before="2026-09-21", include_late=True)
    params = seen[0].url.params
    assert params["after"] == "2026-09-14"
    assert params["before"] == "2026-09-21"
    assert params["include_late"] == "true"


def test_chores_omit_include_late_when_not_asked() -> None:
    seen: list[httpx.Request] = []
    with build(route({("GET", "/api/frames/77/chores"): CHORE_PAYLOAD}, seen)) as client:
        client.chores("77", after="2026-09-19", before="2026-09-19")
    assert "include_late" not in seen[0].url.params


def test_authorization_header_is_sent() -> None:
    seen: list[httpx.Request] = []
    with build(route({("GET", "/api/frames"): FRAMES}, seen)) as client:
        client.frames()
    assert seen[0].headers["authorization"] == "Bearer tok-123"


def test_resolve_frame_id_picks_the_only_frame() -> None:
    with build(route({("GET", "/api/frames"): FRAMES})) as client:
        assert client.resolve_frame_id() == "77"
        assert client.resolve_frame_id("explicit") == "explicit"


def test_resolve_frame_id_refuses_to_guess_between_frames() -> None:
    two = {"data": [{"id": "1", "attributes": {"name": "A"}}, {"id": "2", "attributes": {}}]}
    with build(route({("GET", "/api/frames"): two})) as client:
        with pytest.raises(SkylightError, match="several frames"):
            client.resolve_frame_id()


def test_resolve_member_id_is_case_insensitive() -> None:
    with build(route({("GET", "/api/frames/77/categories"): MEMBERS})) as client:
        assert client.resolve_member_id("77", "lucas") == "1"
        assert client.resolve_member_id("77", "  CAMILA ") == "2"
        assert client.resolve_member_id("77", "2") == "2"


def test_resolve_member_id_lists_known_names_when_missing() -> None:
    with build(route({("GET", "/api/frames/77/categories"): MEMBERS})) as client:
        with pytest.raises(SkylightNotFoundError, match="Lucas, Camila"):
            client.resolve_member_id("77", "Michael")


def test_add_chore_sends_only_the_fields_given() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"data": {"id": "42", "attributes": {"summary": "Take out trash"}}}
        )

    with build(handler) as client:
        created = client.add_chore("77", "Take out trash", start="2026-09-20", category_id="1")

    import json as _json

    body = _json.loads(seen[0].content)
    assert body == {"summary": "Take out trash", "start": "2026-09-20", "category_id": "1"}
    assert created["id"] == "42"


def test_add_chore_marks_recurring_when_given_weekdays() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"id": "43", "attributes": {}}})

    with build(handler) as client:
        client.add_chore("77", "Trash", category_id="1", recurrence_set=["monday", "thursday"])

    import json as _json

    body = _json.loads(seen[0].content)
    assert body["recurring"] is True
    assert body["recurrence_set"] == ["monday", "thursday"]


def test_add_chore_requires_a_category() -> None:
    # Skylight answers a chore with no category with a 422, so it is not optional.
    with build(lambda request: httpx.Response(200, json={})) as client:
        with pytest.raises(TypeError):
            client.add_chore("77", "Trash")


def test_split_chore_id_separates_a_recurring_instance() -> None:
    assert split_chore_id("107378250-2026-09-19-0600") == ("107378250", "2026-09-19", "06:00")
    assert split_chore_id("107378250-2026-09-19-1430") == ("107378250", "2026-09-19", "14:30")


def test_split_chore_id_leaves_a_one_off_alone() -> None:
    assert split_chore_id("107825814") == ("107825814", None, None)
    assert split_chore_id(107825814) == ("107825814", None, None)
    # Anything that does not match the instance shape is passed through whole.
    assert split_chore_id("not-an-id") == ("not-an-id", None, None)


def test_complete_chore_sends_only_status_for_a_one_off() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    with build(handler) as client:
        client.complete_chore("77", "42")

    import json as _json

    assert seen[0].method == "PUT"
    assert seen[0].url.path == "/api/frames/77/chores/42/completions"
    # The API rejects instance_date, instance_time and category_id on a one-off.
    assert _json.loads(seen[0].content) == {"status": "complete"}


def test_complete_chore_splits_a_recurring_instance_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    with build(handler) as client:
        client.complete_chore("77", "107378250-2026-09-19-0600")

    import json as _json

    # The series id addresses the endpoint; the date and time go in the body.
    assert seen[0].url.path == "/api/frames/77/chores/107378250/completions"
    assert _json.loads(seen[0].content) == {
        "status": "complete",
        "instance_date": "2026-09-19",
        "instance_time": "06:00",
    }


def test_complete_chore_can_reopen_one() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    with build(handler) as client:
        client.complete_chore("77", "42", status="pending")

    import json as _json

    assert _json.loads(seen[0].content) == {"status": "pending"}


def test_delete_chore_uses_the_series_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    with build(handler) as client:
        client.delete_chore("77", "107378250-2026-09-19-0600", apply_to="all")

    assert seen[0].method == "DELETE"
    assert seen[0].url.path == "/api/frames/77/chores/107378250"
    assert seen[0].url.params["apply_to"] == "all"


def test_validation_errors_are_surfaced_from_a_list() -> None:
    body = {"errors": ["Category is required."]}
    with build(lambda request: httpx.Response(422, json=body)) as client:
        with pytest.raises(SkylightAPIError, match="Category is required."):
            client.chores("77", after="2026-09-19", before="2026-09-19")


def test_validation_errors_are_surfaced_from_a_field_map() -> None:
    body = {"errors": {"instance_date": ["must be blank"], "status": ["is not included"]}}
    with build(lambda request: httpx.Response(422, json=body)) as client:
        with pytest.raises(SkylightAPIError) as caught:
            client.chores("77", after="2026-09-19", before="2026-09-19")
    assert "instance_date must be blank" in str(caught.value)
    assert "status is not included" in str(caught.value)


def test_a_body_without_errors_still_reports_the_status() -> None:
    with build(lambda request: httpx.Response(422, text="<html>nope</html>")) as client:
        with pytest.raises(SkylightAPIError, match="HTTP 422"):
            client.chores("77", after="2026-09-19", before="2026-09-19")


def test_resolve_list_id_by_name_or_id() -> None:
    with build(route({("GET", "/api/frames/77/lists"): LISTS})) as client:
        assert client.resolve_list_id("77", "groceries") == "9"
        assert client.resolve_list_id("77", "To Do") == "10"
        with pytest.raises(SkylightNotFoundError, match="Groceries, To Do"):
            client.resolve_list_id("77", "Wishlist")


def test_add_list_item_posts_a_label() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"id": "5", "attributes": {"label": "Milk"}}})

    with build(handler) as client:
        item = client.add_list_item("77", "9", "Milk")

    import json as _json

    assert seen[0].url.path == "/api/frames/77/lists/9/list_items"
    assert _json.loads(seen[0].content) == {"label": "Milk"}
    assert item["label"] == "Milk"


def test_complete_list_item_sets_status() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    with build(handler) as client:
        client.complete_list_item("77", "9", "5")
        client.complete_list_item("77", "9", "5", done=False)

    import json as _json

    assert _json.loads(seen[0].content) == {"status": "completed"}
    assert _json.loads(seen[1].content) == {"status": "pending"}


def test_expired_session_is_reported_clearly() -> None:
    with build(lambda request: httpx.Response(401, json={})) as client:
        with pytest.raises(SkylightAuthError, match="skylight login"):
            client.frames()


def test_forbidden_mentions_the_subscription() -> None:
    with build(lambda request: httpx.Response(403, json={})) as client:
        with pytest.raises(SkylightAPIError, match="Skylight Plus"):
            client.frames()


def test_server_error_carries_status_and_body() -> None:
    with build(lambda request: httpx.Response(500, text="boom")) as client:
        with pytest.raises(SkylightAPIError) as caught:
            client.frames()
    assert caught.value.status_code == 500
    assert caught.value.body == "boom"


def test_network_failure_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with build(handler) as client:
        with pytest.raises(SkylightError, match="Network error"):
            client.frames()


def test_empty_body_is_not_parsed_as_json() -> None:
    with build(lambda request: httpx.Response(204)) as client:
        assert client.delete_list_item("77", "9", "5") is None
