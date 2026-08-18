import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app import models

test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
engine = create_engine(
    f"sqlite:///{test_db.name}", connect_args={"check_same_thread": False}
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSession()
    db.add(models.Room(name="Boardroom", capacity=12, floor=3))
    db.commit()
    db.close()
    yield


client = TestClient(app)

BOOKING = {
    "room_id": 1,
    "organizer": "sam@example.com",
    "title": "Sprint sync",
    "start_time": "2026-07-30T14:00:00",
    "end_time": "2026-07-30T15:00:00",
}


def test_create_booking():
    resp = client.post("/bookings", json=BOOKING)
    assert resp.status_code == 201
    body = resp.json()
    assert body["room_id"] == 1
    assert body["title"] == "Sprint sync"


def test_create_booking_unknown_room():
    resp = client.post("/bookings", json={**BOOKING, "room_id": 999})
    assert resp.status_code == 404


def test_rejects_end_before_start():
    bad = {**BOOKING, "end_time": "2026-07-30T13:00:00"}
    resp = client.post("/bookings", json=bad)
    assert resp.status_code == 422


def test_list_bookings():
    client.post("/bookings", json=BOOKING)
    resp = client.get("/bookings")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_cancel_booking():
    created = client.post("/bookings", json=BOOKING).json()
    resp = client.delete(f"/bookings/{created['id']}")
    assert resp.status_code == 204
    assert client.get("/bookings").json() == []


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def test_conflict_rejected_by_default():
    """Overlapping booking without waitlist=True returns 409."""
    client.post("/bookings", json=BOOKING)
    resp = client.post("/bookings", json=BOOKING)
    assert resp.status_code == 409


def test_conflict_partial_overlap_rejected():
    """Partially overlapping window (not identical) is still a conflict."""
    client.post("/bookings", json=BOOKING)
    overlap = {**BOOKING, "start_time": "2026-07-30T14:30:00", "end_time": "2026-07-30T15:30:00"}
    assert client.post("/bookings", json=overlap).status_code == 409


def test_no_conflict_adjacent_slot():
    """A booking that starts exactly when another ends is NOT a conflict."""
    client.post("/bookings", json=BOOKING)
    adjacent = {**BOOKING, "start_time": "2026-07-30T15:00:00", "end_time": "2026-07-30T16:00:00"}
    assert client.post("/bookings", json=adjacent).status_code == 201


# ---------------------------------------------------------------------------
# Waitlist opt-in
# ---------------------------------------------------------------------------

def test_waitlist_opt_in_returns_202():
    """Conflicting request with waitlist=True returns 202 and a WaitlistEntryOut body."""
    client.post("/bookings", json=BOOKING)
    resp = client.post("/bookings", json={**BOOKING, "waitlist": True})
    assert resp.status_code == 202
    body = resp.json()
    assert body["room_id"] == 1
    assert body["title"] == "Sprint sync"
    assert body["position"] == 1


def test_waitlist_position_increments():
    """Second waitlisted entry for the same slot gets position=2."""
    client.post("/bookings", json=BOOKING)
    r1 = client.post("/bookings", json={**BOOKING, "waitlist": True})
    r2 = client.post("/bookings", json={**BOOKING, "waitlist": True, "organizer": "bob@example.com"})
    assert r1.json()["position"] == 1
    assert r2.json()["position"] == 2


# ---------------------------------------------------------------------------
# Auto-promotion on cancellation
# ---------------------------------------------------------------------------

def test_cancel_promotes_first_waitlist_entry():
    """Cancelling a booking promotes the first waitlist entry to a confirmed booking."""
    confirmed = client.post("/bookings", json=BOOKING).json()
    client.post("/bookings", json={**BOOKING, "waitlist": True, "organizer": "bob@example.com"})

    client.delete(f"/bookings/{confirmed['id']}")

    bookings = client.get("/bookings").json()
    assert len(bookings) == 1
    assert bookings[0]["organizer"] == "bob@example.com"
    # Waitlist should now be empty
    assert client.get("/waitlist").json() == []


def test_cancel_promotes_oldest_entry_first():
    """When two entries are waitlisted, position-1 (oldest) is promoted."""
    confirmed = client.post("/bookings", json=BOOKING).json()
    client.post("/bookings", json={**BOOKING, "waitlist": True, "organizer": "first@example.com"})
    client.post("/bookings", json={**BOOKING, "waitlist": True, "organizer": "second@example.com"})

    client.delete(f"/bookings/{confirmed['id']}")

    bookings = client.get("/bookings").json()
    assert bookings[0]["organizer"] == "first@example.com"
    # One entry remains on the waitlist
    assert len(client.get("/waitlist").json()) == 1


def test_cancel_without_waitlist_entry():
    """Cancelling a booking with no waitlist entry still returns 204."""
    confirmed = client.post("/bookings", json=BOOKING).json()
    resp = client.delete(f"/bookings/{confirmed['id']}")
    assert resp.status_code == 204
    assert client.get("/bookings").json() == []


# ---------------------------------------------------------------------------
# GET /waitlist
# ---------------------------------------------------------------------------

def test_list_waitlist_empty():
    assert client.get("/waitlist").json() == []


def test_list_waitlist_filtered_by_room():
    """?room_id= filters to only that room's entries."""
    client.post("/bookings", json=BOOKING)
    client.post("/bookings", json={**BOOKING, "waitlist": True})
    resp = client.get("/waitlist?room_id=1")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    assert all(e["room_id"] == 1 for e in entries)
    assert entries[0]["position"] == 1


def test_list_waitlist_positions_per_room():
    """Positions are numbered per-room from 1."""
    client.post("/bookings", json=BOOKING)
    client.post("/bookings", json={**BOOKING, "waitlist": True, "organizer": "a@example.com"})
    client.post("/bookings", json={**BOOKING, "waitlist": True, "organizer": "b@example.com"})
    entries = client.get("/waitlist?room_id=1").json()
    assert [e["position"] for e in entries] == [1, 2]


# ---------------------------------------------------------------------------
# DELETE /waitlist/{id}
# ---------------------------------------------------------------------------

def test_delete_waitlist_entry():
    """DELETE /waitlist/{id} removes the entry (204)."""
    client.post("/bookings", json=BOOKING)
    entry = client.post("/bookings", json={**BOOKING, "waitlist": True}).json()
    resp = client.delete(f"/waitlist/{entry['id']}")
    assert resp.status_code == 204
    assert client.get("/waitlist").json() == []


def test_delete_waitlist_entry_not_found():
    resp = client.delete("/waitlist/999")
    assert resp.status_code == 404
