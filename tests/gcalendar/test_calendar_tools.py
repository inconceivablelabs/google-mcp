"""
Unit tests for Google Calendar MCP tools.

Covers the extracted ``_fetch_event_items`` helper, the new ``get_events_raw``
tool, and a regression assertion that ``get_events`` formatted output is
unchanged after the helper extraction.
"""

import json
import os
import sys

import pytest
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import (
    _fetch_event_items,
    get_events,
    get_events_raw,
    respond_event,
)
from core.utils import UserInputError


def _unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original async function.

    Handles both older FastMCP (FunctionTool with .fn) and newer FastMCP
    (server.tool() returns the function directly).
    """
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


# ---------------------------------------------------------------------------
# Fixture events
# ---------------------------------------------------------------------------


def _one_attendee_event() -> dict:
    return {
        "kind": "calendar#event",
        "etag": '"3001"',
        "id": "evt_one_attendee",
        "status": "confirmed",
        "htmlLink": "https://calendar.google.com/event?eid=evt_one_attendee",
        "created": "2026-05-10T09:00:00.000Z",
        "updated": "2026-05-10T09:05:00.000Z",
        "summary": "1:1 with Alex",
        "description": "Weekly sync",
        "location": "Meet",
        "organizer": {
            "email": "user@example.com",
            "displayName": "User Example",
            "self": True,
        },
        "start": {
            "dateTime": "2026-05-20T10:00:00-05:00",
            "timeZone": "America/Chicago",
        },
        "end": {"dateTime": "2026-05-20T10:30:00-05:00", "timeZone": "America/Chicago"},
        "attendees": [
            {
                "email": "alex@example.com",
                "displayName": "Alex Person",
                "responseStatus": "accepted",
                "optional": False,
                "self": False,
            },
        ],
        "eventType": "default",
    }


def _multi_attendee_event_with_decline() -> dict:
    return {
        "kind": "calendar#event",
        "etag": '"3002"',
        "id": "evt_multi_attendee",
        "status": "confirmed",
        "htmlLink": "https://calendar.google.com/event?eid=evt_multi_attendee",
        "created": "2026-05-11T14:00:00.000Z",
        "updated": "2026-05-11T14:02:00.000Z",
        "summary": "Product review",
        "organizer": {
            "email": "user@example.com",
            "displayName": "User Example",
            "self": True,
        },
        "start": {"dateTime": "2026-05-21T15:00:00-05:00"},
        "end": {"dateTime": "2026-05-21T16:00:00-05:00"},
        "attendees": [
            {
                "email": "bea@example.com",
                "displayName": "Bea Person",
                "responseStatus": "accepted",
                "optional": False,
            },
            {
                "email": "carl@example.com",
                "displayName": "Carl Person",
                "responseStatus": "declined",
                "optional": True,
            },
            {
                "email": "dana@example.com",
                "displayName": "Dana Person",
                "responseStatus": "needsAction",
                "optional": False,
            },
        ],
        "eventType": "default",
    }


def _all_day_event() -> dict:
    return {
        "kind": "calendar#event",
        "etag": '"3003"',
        "id": "evt_all_day",
        "status": "confirmed",
        "htmlLink": "https://calendar.google.com/event?eid=evt_all_day",
        "created": "2026-05-12T08:00:00.000Z",
        "updated": "2026-05-12T08:00:00.000Z",
        "summary": "Conference day 1",
        "organizer": {"email": "user@example.com", "self": True},
        "start": {"date": "2026-05-25"},
        "end": {"date": "2026-05-26"},
        "eventType": "default",
    }


def _recurring_instance_event() -> dict:
    return {
        "kind": "calendar#event",
        "etag": '"3004"',
        "id": "master_id_20260526T140000Z",
        "status": "confirmed",
        "htmlLink": "https://calendar.google.com/event?eid=recurring",
        "created": "2026-01-01T10:00:00.000Z",
        "updated": "2026-05-01T10:00:00.000Z",
        "summary": "Weekly team standup",
        "organizer": {"email": "user@example.com", "self": True},
        "start": {"dateTime": "2026-05-26T09:00:00-05:00"},
        "end": {"dateTime": "2026-05-26T09:30:00-05:00"},
        "recurringEventId": "master_id",
        "eventType": "default",
    }


def _fixture_events() -> list[dict]:
    return [
        _one_attendee_event(),
        _multi_attendee_event_with_decline(),
        _all_day_event(),
        _recurring_instance_event(),
    ]


# ---------------------------------------------------------------------------
# _fetch_event_items helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_event_items_list_mode_uses_single_events_and_order_by():
    """List mode calls service.events().list with singleEvents=True and orderBy=startTime."""
    fixture = _fixture_events()
    mock_service = Mock()
    mock_service.events().list().execute.return_value = {"items": fixture}

    items = await _fetch_event_items(
        service=mock_service,
        time_min="2026-05-20T00:00:00Z",
        time_max="2026-05-30T00:00:00Z",
    )

    assert items == fixture
    call_kwargs = mock_service.events.return_value.list.call_args.kwargs
    assert call_kwargs["singleEvents"] is True
    assert call_kwargs["orderBy"] == "startTime"
    assert call_kwargs["calendarId"] == "primary"


@pytest.mark.asyncio
async def test_fetch_event_items_single_event_mode_uses_get():
    """When event_id is provided, the helper calls service.events().get and returns a single-element list."""
    one = _one_attendee_event()
    mock_service = Mock()
    mock_service.events().get().execute.return_value = one

    items = await _fetch_event_items(service=mock_service, event_id="evt_one_attendee")

    assert items == [one]
    # Verify .get was called with the right calendarId + eventId
    get_kwargs = mock_service.events.return_value.get.call_args.kwargs
    assert get_kwargs["calendarId"] == "primary"
    assert get_kwargs["eventId"] == "evt_one_attendee"


@pytest.mark.asyncio
async def test_fetch_event_items_time_format_prep_threads_to_api():
    """A non-Z time_min/time_max is reformatted (Z-suffix appended) before reaching the API."""
    mock_service = Mock()
    mock_service.events().list().execute.return_value = {"items": []}

    await _fetch_event_items(
        service=mock_service,
        time_min="2026-05-20T10:00:00",
        time_max="2026-05-21T10:00:00",
    )

    call_kwargs = mock_service.events.return_value.list.call_args.kwargs
    assert call_kwargs["timeMin"] == "2026-05-20T10:00:00Z"
    assert call_kwargs["timeMax"] == "2026-05-21T10:00:00Z"


@pytest.mark.asyncio
async def test_fetch_event_items_query_param_passed_to_api():
    """The query parameter is forwarded to the Calendar API as ``q``."""
    mock_service = Mock()
    mock_service.events().list().execute.return_value = {"items": []}

    await _fetch_event_items(service=mock_service, query="standup")

    call_kwargs = mock_service.events.return_value.list.call_args.kwargs
    assert call_kwargs["q"] == "standup"


# ---------------------------------------------------------------------------
# get_events_raw end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_events_raw_preserves_all_required_fields():
    """JSON output preserves id, created, organizer.email, attendee flags, start fields, recurringEventId."""
    fixture = _fixture_events()
    mock_service = Mock()
    mock_service.events().list().execute.return_value = {"items": fixture}

    result = await _unwrap(get_events_raw)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    payload = json.loads(result)
    events = payload["events"]
    assert len(events) == 4

    one = events[0]
    assert one["id"] == "evt_one_attendee"
    assert one["created"] == "2026-05-10T09:00:00.000Z"
    assert one["organizer"]["email"] == "user@example.com"
    assert one["organizer"]["displayName"] == "User Example"
    assert one["start"]["dateTime"] == "2026-05-20T10:00:00-05:00"
    assert one["attendees"][0]["responseStatus"] == "accepted"
    assert one["attendees"][0]["optional"] is False
    assert one["attendees"][0]["displayName"] == "Alex Person"

    multi = events[1]
    declined = [a for a in multi["attendees"] if a["responseStatus"] == "declined"]
    assert len(declined) == 1
    assert declined[0]["optional"] is True

    all_day = events[2]
    assert all_day["start"]["date"] == "2026-05-25"
    assert "dateTime" not in all_day["start"]

    recurring = events[3]
    assert recurring["recurringEventId"] == "master_id"
    assert recurring["id"] == "master_id_20260526T140000Z"


@pytest.mark.asyncio
async def test_get_events_raw_json_round_trip():
    """The serialized output parses cleanly with json.loads."""
    fixture = _fixture_events()
    mock_service = Mock()
    mock_service.events().list().execute.return_value = {"items": fixture}

    result = await _unwrap(get_events_raw)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    payload = json.loads(result)
    assert "events" in payload
    assert isinstance(payload["events"], list)
    assert len(payload["events"]) == len(fixture)


@pytest.mark.asyncio
async def test_get_events_raw_single_event_mode():
    """event_id is honored end-to-end and yields a single-event list."""
    one = _one_attendee_event()
    mock_service = Mock()
    mock_service.events().get().execute.return_value = one

    result = await _unwrap(get_events_raw)(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt_one_attendee",
    )

    payload = json.loads(result)
    assert len(payload["events"]) == 1
    assert payload["events"][0]["id"] == "evt_one_attendee"


# ---------------------------------------------------------------------------
# Regression: get_events formatted output unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_events_formatted_output_basic():
    """Regression: basic (non-detailed) list output retains its header, dash-quote lines, IDs, and links."""
    fixture = _fixture_events()
    mock_service = Mock()
    mock_service.events().list().execute.return_value = {"items": fixture}

    result = await _unwrap(get_events)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert "Successfully retrieved 4 events" in result
    assert "for user@example.com" in result
    assert '- "1:1 with Alex" (Starts: 2026-05-20T10:00:00-05:00' in result
    assert "ID: evt_one_attendee" in result
    assert "https://calendar.google.com/event?eid=evt_one_attendee" in result
    # All four fixture events show up
    assert "Product review" in result
    assert "Conference day 1" in result
    assert "Weekly team standup" in result


@pytest.mark.asyncio
async def test_get_events_formatted_output_no_events():
    """Regression: empty result preserves the 'No events found' message."""
    mock_service = Mock()
    mock_service.events().list().execute.return_value = {"items": []}

    result = await _unwrap(get_events)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert "No events found" in result
    assert "primary" in result


@pytest.mark.asyncio
async def test_get_events_formatted_output_single_event_basic():
    """Regression: single-event (event_id) basic output keeps the 'Successfully retrieved event' header."""
    one = _one_attendee_event()
    mock_service = Mock()
    mock_service.events().get().execute.return_value = one

    result = await _unwrap(get_events)(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt_one_attendee",
    )

    assert "Successfully retrieved event" in result
    assert "1:1 with Alex" in result
    assert "evt_one_attendee" in result


# ---------------------------------------------------------------------------
# respond_event tests (gmc-0i8)
# ---------------------------------------------------------------------------


def _three_attendee_event() -> dict:
    """Event where the user is one of three attendees and has not yet responded."""
    return {
        "kind": "calendar#event",
        "id": "evt_three",
        "summary": "Team Sync",
        "htmlLink": "https://calendar.google.com/event?eid=evt_three",
        "attendees": [
            {
                "email": "user@example.com",
                "responseStatus": "needsAction",
                "self": True,
            },
            {"email": "alex@example.com", "responseStatus": "accepted"},
            {"email": "casey@example.com", "responseStatus": "declined"},
        ],
    }


def _patched_event_return() -> dict:
    return {
        "kind": "calendar#event",
        "id": "evt_three",
        "summary": "Team Sync",
        "htmlLink": "https://calendar.google.com/event?eid=evt_three",
    }


@pytest.mark.asyncio
async def test_respond_event_accept_preserves_other_attendees_via_patch():
    """Accepting an invite patches the full attendee list, changing only self's responseStatus."""
    event = _three_attendee_event()
    mock_service = Mock()
    mock_service.events().get().execute.return_value = event
    # Configure patch's return WITHOUT calling patch() (so call_count stays accurate).
    mock_service.events.return_value.patch.return_value.execute.return_value = (
        _patched_event_return()
    )

    result = await _unwrap(respond_event)(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt_three",
        response_status="accepted",
    )

    # (a) patch called exactly once; update never called.
    assert mock_service.events.return_value.patch.call_count == 1
    assert not mock_service.events.return_value.update.called

    patch_kwargs = mock_service.events.return_value.patch.call_args.kwargs
    body = patch_kwargs["body"]
    attendees = body["attendees"]

    # (b) all 3 entries present; only self changed.
    assert len(attendees) == 3
    by_email = {a["email"]: a for a in attendees}
    assert by_email["user@example.com"]["responseStatus"] == "accepted"
    assert by_email["alex@example.com"] == {
        "email": "alex@example.com",
        "responseStatus": "accepted",
    }
    assert by_email["casey@example.com"] == {
        "email": "casey@example.com",
        "responseStatus": "declined",
    }
    # self entry keeps its other fields (self flag) untouched.
    assert by_email["user@example.com"]["self"] is True

    # (c) patch kwargs include calendarId, eventId, sendUpdates.
    assert patch_kwargs["calendarId"] == "primary"
    assert patch_kwargs["eventId"] == "evt_three"
    assert patch_kwargs["sendUpdates"] == "all"

    # (d) body contains ONLY the attendees key (no shared/organizer-owned keys).
    assert set(body.keys()) == {"attendees"}

    assert "Team Sync" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "given,expected",
    [
        ("accept", "accepted"),
        ("decline", "declined"),
        ("tentative", "tentative"),
        ("accepted", "accepted"),
    ],
)
async def test_respond_event_status_aliases_map_correctly(given, expected):
    """Ergonomic aliases map to the correct Google responseStatus value."""
    event = _three_attendee_event()
    mock_service = Mock()
    mock_service.events().get().execute.return_value = event
    mock_service.events.return_value.patch.return_value.execute.return_value = (
        _patched_event_return()
    )

    await _unwrap(respond_event)(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt_three",
        response_status=given,
    )

    body = mock_service.events.return_value.patch.call_args.kwargs["body"]
    by_email = {a["email"]: a for a in body["attendees"]}
    assert by_email["user@example.com"]["responseStatus"] == expected


@pytest.mark.asyncio
async def test_respond_event_self_matched_by_self_flag():
    """Self entry is matched via self:True even when the email differs/case-mismatches."""
    event = {
        "id": "evt_self_flag",
        "summary": "Offsite",
        "attendees": [
            {
                "email": "OTHER-ALIAS@example.com",
                "responseStatus": "needsAction",
                "self": True,
            },
            {"email": "alex@example.com", "responseStatus": "accepted"},
        ],
    }
    mock_service = Mock()
    mock_service.events().get().execute.return_value = event
    mock_service.events.return_value.patch.return_value.execute.return_value = {
        "summary": "Offsite",
        "id": "evt_self_flag",
    }

    await _unwrap(respond_event)(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt_self_flag",
        response_status="tentative",
    )

    body = mock_service.events.return_value.patch.call_args.kwargs["body"]
    by_email = {a["email"]: a for a in body["attendees"]}
    assert by_email["OTHER-ALIAS@example.com"]["responseStatus"] == "tentative"
    # other attendee untouched
    assert by_email["alex@example.com"]["responseStatus"] == "accepted"


@pytest.mark.asyncio
async def test_respond_event_user_not_attendee_raises():
    """If the user is not among attendees (and no self:true), raise UserInputError and don't patch."""
    event = {
        "id": "evt_no_self",
        "summary": "Not Invited",
        "attendees": [
            {"email": "alex@example.com", "responseStatus": "accepted"},
            {"email": "casey@example.com", "responseStatus": "declined"},
        ],
    }
    mock_service = Mock()
    mock_service.events().get().execute.return_value = event

    with pytest.raises(UserInputError):
        await _unwrap(respond_event)(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="evt_no_self",
            response_status="accepted",
        )

    assert not mock_service.events.return_value.patch.called


@pytest.mark.asyncio
async def test_respond_event_invalid_response_status_raises():
    """An unrecognized response_status raises UserInputError before any API call."""
    mock_service = Mock()

    with pytest.raises(UserInputError):
        await _unwrap(respond_event)(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="evt_three",
            response_status="maybe",
        )

    assert not mock_service.events.return_value.get.called
    assert not mock_service.events.return_value.patch.called


@pytest.mark.asyncio
async def test_respond_event_invalid_send_updates_raises():
    """An invalid send_updates value raises UserInputError."""
    mock_service = Mock()

    with pytest.raises(UserInputError):
        await _unwrap(respond_event)(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="evt_three",
            response_status="accepted",
            send_updates="bogus",
        )

    assert not mock_service.events.return_value.patch.called


@pytest.mark.asyncio
@pytest.mark.parametrize("attendees_value", [None, []])
async def test_respond_event_no_attendees_raises(attendees_value):
    """An event with no/empty attendees raises UserInputError and never patches."""
    event = {"id": "evt_empty", "summary": "Solo Block"}
    if attendees_value is not None:
        event["attendees"] = attendees_value
    mock_service = Mock()
    mock_service.events().get().execute.return_value = event

    with pytest.raises(UserInputError):
        await _unwrap(respond_event)(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="evt_empty",
            response_status="accepted",
        )

    assert not mock_service.events.return_value.patch.called


@pytest.mark.asyncio
async def test_respond_event_self_flag_takes_precedence_over_email_match():
    """When an email-matching entry precedes the self:true entry, the self entry wins."""
    event = {
        "id": "evt_precedence",
        "summary": "Precedence Check",
        "attendees": [
            # Email-matching alias entry appears FIRST, but has no self flag.
            {"email": "user@example.com", "responseStatus": "needsAction"},
            # Authoritative self entry has a different email.
            {
                "email": "primary-alias@example.com",
                "responseStatus": "needsAction",
                "self": True,
            },
        ],
    }
    mock_service = Mock()
    mock_service.events().get().execute.return_value = event
    mock_service.events.return_value.patch.return_value.execute.return_value = {
        "summary": "Precedence Check",
        "id": "evt_precedence",
    }

    await _unwrap(respond_event)(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt_precedence",
        response_status="accepted",
    )

    body = mock_service.events.return_value.patch.call_args.kwargs["body"]
    by_email = {a["email"]: a for a in body["attendees"]}
    # The self:true entry is the one that changed.
    assert by_email["primary-alias@example.com"]["responseStatus"] == "accepted"
    # The email-only entry is left unchanged.
    assert by_email["user@example.com"]["responseStatus"] == "needsAction"
