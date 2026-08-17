from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import models
from .database import Base, SessionLocal, engine
from .routes import router

SEED_ROOMS = [
    {"name": "Boardroom", "capacity": 12, "floor": 3},
    {"name": "Huddle A", "capacity": 4, "floor": 2},
    {"name": "Huddle B", "capacity": 4, "floor": 2},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(models.Room).count() == 0:
            for room in SEED_ROOMS:
                db.add(models.Room(**room))
            db.commit()
    finally:
        db.close()
    yield


app = FastAPI(title="Room Scheduler", lifespan=lifespan)
app.include_router(router)
