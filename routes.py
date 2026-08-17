from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from . import models, schemas
from .database import get_db

router = APIRouter()


@router.get("/rooms", response_model=list[schemas.RoomOut])
def list_rooms(db: Session = Depends(get_db)):
    return db.query(models.Room).order_by(models.Room.name).all()


@router.get("/bookings", response_model=list[schemas.BookingOut])
def list_bookings(room_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(models.Booking)
    if room_id is not None:
        query = query.filter(models.Booking.room_id == room_id)
    return query.order_by(models.Booking.start_time).all()


@router.post(
    "/bookings",
    response_model=schemas.BookingOut,
    status_code=201,
    responses={202: {"model": schemas.WaitlistEntryOut}},
)
def create_booking(payload: schemas.BookingCreate, db: Session = Depends(get_db)):
    room = db.get(models.Room, payload.room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    conflict = (
        db.query(models.Booking)
        .filter(
            models.Booking.room_id == payload.room_id,
            models.Booking.start_time < payload.end_time,
            models.Booking.end_time > payload.start_time,
        )
        .first()
    )

    if conflict:
        if not payload.waitlist:
            raise HTTPException(status_code=409, detail="Room already booked for that time slot")

        entry = models.WaitlistEntry(
            room_id=payload.room_id,
            organizer=payload.organizer,
            title=payload.title,
            start_time=payload.start_time,
            end_time=payload.end_time,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)

        position = (
            db.query(models.WaitlistEntry)
            .filter(
                models.WaitlistEntry.room_id == payload.room_id,
                models.WaitlistEntry.start_time < payload.end_time,
                models.WaitlistEntry.end_time > payload.start_time,
                models.WaitlistEntry.id < entry.id,
            )
            .count()
            + 1
        )

        return Response(
            content=schemas.WaitlistEntryOut(
                id=entry.id,
                room_id=entry.room_id,
                organizer=entry.organizer,
                title=entry.title,
                start_time=entry.start_time,
                end_time=entry.end_time,
                position=position,
            ).model_dump_json(),
            status_code=202,
            media_type="application/json",
        )

    booking = models.Booking(**payload.model_dump(exclude={"waitlist"}))
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking


@router.delete("/bookings/{booking_id}", status_code=204)
def cancel_booking(booking_id: int, db: Session = Depends(get_db)):
    booking = db.get(models.Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")

    room_id = booking.room_id
    start_time = booking.start_time
    end_time = booking.end_time

    db.delete(booking)

    next_entry = (
        db.query(models.WaitlistEntry)
        .filter(
            models.WaitlistEntry.room_id == room_id,
            models.WaitlistEntry.start_time < end_time,
            models.WaitlistEntry.end_time > start_time,
        )
        .order_by(models.WaitlistEntry.created_at)
        .first()
    )

    if next_entry is not None:
        new_booking = models.Booking(
            room_id=next_entry.room_id,
            organizer=next_entry.organizer,
            title=next_entry.title,
            start_time=next_entry.start_time,
            end_time=next_entry.end_time,
        )
        db.delete(next_entry)
        db.add(new_booking)

    db.commit()


@router.get("/waitlist", response_model=list[schemas.WaitlistEntryOut])
def list_waitlist(room_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(models.WaitlistEntry)
    if room_id is not None:
        query = query.filter(models.WaitlistEntry.room_id == room_id)
    entries = query.order_by(models.WaitlistEntry.room_id, models.WaitlistEntry.created_at).all()

    # Compute per-room position
    result = []
    counters: dict[int, int] = {}
    for entry in entries:
        counters[entry.room_id] = counters.get(entry.room_id, 0) + 1
        result.append(
            schemas.WaitlistEntryOut(
                id=entry.id,
                room_id=entry.room_id,
                organizer=entry.organizer,
                title=entry.title,
                start_time=entry.start_time,
                end_time=entry.end_time,
                position=counters[entry.room_id],
            )
        )
    return result


@router.delete("/waitlist/{entry_id}", status_code=204)
def cancel_waitlist_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.get(models.WaitlistEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Waitlist entry not found")

    db.delete(entry)
    db.commit()
