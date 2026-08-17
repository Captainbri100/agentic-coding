# room-scheduler

A small FastAPI service for booking conference rooms.

## Run it

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload

The API serves on http://localhost:8000 (interactive docs at /docs).
Three rooms are seeded on first startup.

## Endpoints

- `GET /rooms` — list rooms
- `GET /bookings` — list bookings (optional `?room_id=`)
- `POST /bookings` — create a booking
- `DELETE /bookings/{id}` — cancel a booking

## Tests

    pytest
