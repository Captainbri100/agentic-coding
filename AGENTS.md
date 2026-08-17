# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Stack
FastAPI + SQLAlchemy 2.x (mapped_column ORM style) + Pydantic v2 + SQLite — no pyproject.toml or linter config exists.

## Commands
```bash
# Run all tests
pytest

# Run a single test by name
pytest tests/test_bookings.py::test_create_booking

# Start dev server
uvicorn app.main:app --reload
```

## Testing gotchas
- Tests override `get_db` at **module load time** (`app.dependency_overrides[get_db] = override_get_db` at top-level in [`tests/test_bookings.py`](tests/test_bookings.py)), not inside fixtures — new test files must do the same before importing `TestClient`.
- The `fresh_db` fixture is `autouse=True` and drops/recreates the schema + seeds exactly **one room (id=1, Boardroom)** before every test. Tests that need a specific room id must account for this.
- Tests use a **named temp file** (not `:memory:`) because SQLite in-memory DBs don't share state across connections.
- No `conftest.py` exists — fixture and override setup lives entirely in the single test file.

## Architecture
- DB tables are created in the `lifespan` handler in [`app/main.py`](app/main.py); three rooms are seeded only when the table is empty.
- SQLAlchemy models use the **SQLAlchemy 2.x `Mapped`/`mapped_column` typed API** — do not mix in the older `Column()` style.
- Pydantic schemas use **`ConfigDict(from_attributes=True)`** (Pydantic v2) — not the v1 `class Config: orm_mode = True`.
- Input validation (end > start) lives in a `@model_validator(mode="after")` on `BookingCreate` in [`app/schemas.py`](app/schemas.py), which surfaces as HTTP 422.
- Routes always use `db.get(Model, pk)` for primary-key lookups (SQLAlchemy 2 style), not `db.query(...).filter(...).first()`.
- `created_at` on `Booking` is set via a `default=lambda: datetime.now(timezone.utc)` on the column — it is **not exposed** in `BookingOut` and is invisible to the API.
