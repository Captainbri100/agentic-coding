from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    capacity: int
    floor: int


class BookingCreate(BaseModel):
    room_id: int
    organizer: str
    title: str
    start_time: datetime
    end_time: datetime
    waitlist: bool = False

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    room_id: int
    organizer: str
    title: str
    start_time: datetime
    end_time: datetime


class WaitlistEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    room_id: int
    organizer: str
    title: str
    start_time: datetime
    end_time: datetime
    position: int
