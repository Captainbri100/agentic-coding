# booking-conflict-waitlist-plan.md

## Overview

Add two capabilities to the room-scheduler API:

1. **Conflict detection** — `POST /bookings` rejects a request (HTTP 409) when an existing confirmed booking overlaps the requested time window for the same room. If the caller includes `"waitlist": true` in the body and a conflict exists, the request is queued instead of rejected.

2. **Waitlist** — a separate `WaitlistEntry` table stores queued requests, ordered by insertion time. A `GET /waitlist` endpoint exposes entries (filterable by `?room_id=`). A `DELETE /waitlist/{id}` endpoint lets callers remove themselves. When `DELETE /bookings/{id}` cancels a confirmed booking, the route automatically promotes the first waitlist entry for that room+window: it deletes the `WaitlistEntry` row and creates a fresh `Booking` row (new id, `created_at` from promotion time).

No changes to the existing `/rooms` or `GET /bookings` endpoints. All new behaviour is additive.

---

## Sub-Tasks

---

### Sub-Task 1 — Add `WaitlistEntry` model

**Status:** [x] done

**Intent**
Introduce the new database table that holds queued booking requests. Getting the model right first keeps the subsequent sub-tasks clean.

**Expected Outcomes**
- `app/models.py` contains a `WaitlistEntry` class following the same `Mapped`/`mapped_column` SQLAlchemy 2 style used by `Booking`.
- `WaitlistEntry` has columns: `id`, `room_id` (FK → rooms.id), `organizer`, `title`, `start_time`, `end_time`, `created_at` (UTC lambda default, same pattern as `Booking.created_at`).
- A `position` value is **not stored** — it is computed at query time via ordering by `created_at` (or `id`).
- `Room` model gains a `waitlist_entries` relationship (`cascade="all, delete-orphan"`) mirroring the existing `bookings` relationship.

**Todo List**
- [ ] In `app/models.py`, add `WaitlistEntry` class with the columns listed above.
- [ ] Add `waitlist_entries: Mapped[list["WaitlistEntry"]]` relationship to `Room`.

**Relevant Context**
- `app/models.py` — mirror the `Booking` model structure exactly; use `Mapped[datetime]` + `mapped_column(DateTime)` for time columns.
- `Room.bookings` relationship (cascade="all, delete-orphan") is the pattern to copy for `waitlist_entries`.

---

### Sub-Task 2 — Add Pydantic schemas for waitlist

**Status:** [x] done

**Intent**
Define the input and output shapes for waitlist operations, and extend `BookingCreate` with the optional opt-in flag.

**Expected Outcomes**
- `BookingCreate` gains an optional field `waitlist: bool = False`.
- A new `WaitlistEntryOut` schema exists with `ConfigDict(from_attributes=True)` and fields: `id`, `room_id`, `organizer`, `title`, `start_time`, `end_time`, `position` (int — computed, not a DB column).
- `position` is an ordinary Pydantic field; the route will compute and inject it before returning.

**Todo List**
- [ ] Add `waitlist: bool = False` to `BookingCreate` in `app/schemas.py`.
- [ ] Add `WaitlistEntryOut` to `app/schemas.py` following the same `ConfigDict(from_attributes=True)` pattern as `BookingOut`.
- [ ] Add `position: int` to `WaitlistEntryOut` (not `Optional` — always provided by the route).

**Relevant Context**
- `app/schemas.py` — `BookingOut` and `RoomOut` are the style references.
- `position` cannot come from `from_attributes` (it's not a DB column); the route must set it explicitly before returning.

---

### Sub-Task 3 — Conflict detection + waitlist opt-in in `POST /bookings`

**Status:** [x] done

**Intent**
Make `create_booking` check for overlapping confirmed bookings and either reject (409) or enqueue (202) depending on the `waitlist` flag.

**Expected Outcomes**
- After the room-existence check and before `db.add`, `create_booking` queries for any existing `Booking` where `room_id` matches and time windows overlap.
- Overlap condition: `existing.start_time < payload.end_time AND existing.end_time > payload.start_time`.
- If conflict found and `payload.waitlist` is `False` → raise `HTTPException(409, "Room already booked for that time slot")`.
- If conflict found and `payload.waitlist` is `True` → create a `WaitlistEntry` row (same fields minus `waitlist` flag), commit, and return it as `WaitlistEntryOut` with `position` set to its rank (count of earlier entries for same room+window + 1); HTTP status 202.
- If no conflict → existing happy path unchanged (HTTP 201 Booking).
- The `position` for a newly enqueued entry is `1` if it is the only entry, or `N` if there are already `N-1` entries ahead of it (ordered by `created_at`/`id`).

**Todo List**
- [ ] In `create_booking` (after room check), add `db.query` for overlapping `Booking` rows using the overlap condition above.
- [ ] Add 409 branch for conflict + `waitlist=False`.
- [ ] Add 202 branch: create `WaitlistEntry`, compute `position`, return `WaitlistEntryOut`.
- [ ] Update the route's `response_model` to `schemas.BookingOut | schemas.WaitlistEntryOut` and add `responses={202: {"model": schemas.WaitlistEntryOut}}`.

**Relevant Context**
- `app/routes.py` `create_booking` — insert conflict query between line 26 (room check) and line 29 (`db.add`).
- Overlap query should use `db.query(models.Booking).filter(...)` to match existing style in `list_bookings`.
- Position query: `db.query(models.WaitlistEntry).filter(room_id, time overlap, id < new_entry.id).count() + 1` — or simply count all entries for that room+window with `created_at` before the new one, then add 1.

---

### Sub-Task 4 — Auto-promote on cancellation in `DELETE /bookings/{id}`

**Status:** [x] done

**Intent**
When a confirmed booking is cancelled, automatically promote the oldest waitlist entry covering the same room and overlapping time window to a new confirmed booking.

**Expected Outcomes**
- After deleting the booking, `cancel_booking` queries `WaitlistEntry` for the same `room_id` with a time window that overlaps the just-deleted booking's window, ordered by `created_at` ascending, and takes the first result.
- If one exists: delete the `WaitlistEntry` row and create a new `Booking` row from its fields (new `id`, new `created_at`). All in the same transaction (single `db.commit`).
- If none exists: cancel proceeds as before (no error, no extra work).
- The promoted booking is not returned in the DELETE response (204 stays 204).

**Todo List**
- [ ] In `cancel_booking`, after `db.delete(booking)` but before `db.commit`, query for the first overlapping `WaitlistEntry` for the same `room_id`, ordered by `created_at`.
- [ ] If found: `db.delete(entry)`, then `db.add(models.Booking(...))` from the entry's fields.
- [ ] Perform a single `db.commit()` to make cancellation + promotion atomic.

**Relevant Context**
- `app/routes.py` `cancel_booking` — the commit currently happens at line 43; batch the delete + optional add + commit into one call.
- Same overlap condition as Sub-Task 3 for the waitlist query.
- `Booking(**payload.model_dump())` pattern from `create_booking` is the reference for constructing the new Booking from the entry's fields — manually map fields since `WaitlistEntry` is not a Pydantic schema.

---

### Sub-Task 5 — `GET /waitlist` and `DELETE /waitlist/{id}` endpoints

**Status:** [x] done

**Intent**
Expose the waitlist for inspection and allow self-removal.

**Expected Outcomes**
- `GET /waitlist?room_id=` returns a list of `WaitlistEntryOut`, each with a `position` field reflecting its rank (1 = first to be promoted) for that room's queue, ordered by `created_at`.
- `DELETE /waitlist/{id}` removes the entry (204). Returns 404 if not found.
- Position is computed per-room: within results for the same `room_id`, `position` increments from 1 by `created_at` order. Across different rooms in the same response (when `room_id` not filtered), each room's entries are numbered independently.

**Todo List**
- [ ] Add `GET /waitlist` route to `app/routes.py` with optional `room_id: int | None = None` query param.
- [ ] Query `WaitlistEntry`, filter by `room_id` if given, order by `created_at`.
- [ ] Annotate each result with its `position` before returning (enumerate or per-room counter).
- [ ] Add `DELETE /waitlist/{entry_id}` route; `db.get` + 404 guard + `db.delete` + `db.commit`, returns 204.

**Relevant Context**
- `list_bookings` in `app/routes.py` is the direct pattern to follow for the GET endpoint with optional filter.
- `cancel_booking` is the pattern for the DELETE endpoint.
- `position` must be set on each `WaitlistEntryOut` instance before it is returned (not from DB).

---

### Sub-Task 6 — Tests

**Status:** [x] done

**Intent**
Cover the new behaviour with focused tests in the existing test file, following every convention already established.

**Expected Outcomes**
Tests added to `tests/test_bookings.py` (no new files, no conftest.py) covering:
- Conflict with `waitlist=False` → 409
- Conflict with `waitlist=True` → 202 + correct `WaitlistEntryOut` body
- Multiple waitlist entries → `position` values are 1, 2, 3...
- Cancel a booking that has a waitlisted entry → the waitlist entry is gone and a new booking exists
- Cancel a booking with no waitlist entry → just 204, no promotion side-effect
- `GET /waitlist?room_id=` → returns only entries for that room, with correct positions
- `DELETE /waitlist/{id}` → 204, entry gone; 404 for unknown id

**Todo List**
- [ ] Add all tests above to `tests/test_bookings.py`.
- [ ] Use the existing `BOOKING` dict and `fresh_db` autouse fixture — no new fixtures needed.
- [ ] Verify `waitlist=False` is the default (existing `test_create_booking` must still pass without change).

**Relevant Context**
- `tests/test_bookings.py` — dependency override is module-level; `fresh_db` seeds one Boardroom (id=1).
- Existing `BOOKING` constant has `room_id: 1` — reuse it; use `{**BOOKING, "waitlist": True}` for waitlist tests.
- Run a single test: `pytest tests/test_bookings.py::test_name`.
