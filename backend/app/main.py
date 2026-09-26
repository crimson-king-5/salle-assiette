import datetime as dt
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.algorithms import compute_readiness
from app.database import Base, DbSession, engine
from app.models import Checkin
from app.schemas import CheckinIn, CheckinOut


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: create the tables if they don't exist yet.
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Salle & Assiette", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    # No frontend yet: send visitors to the interactive API docs.
    return RedirectResponse("/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/checkin")
def create_checkin(checkin: CheckinIn, db: DbSession) -> CheckinOut:
    """Morning check-in → traffic light + workout format (SPEC §5.1), saved in the DB."""
    result = compute_readiness(
        energy=checkin.energy,
        sleep=checkin.sleep,
        motivation=checkin.motivation,
        soreness=checkin.soreness,
        minutes=checkin.minutes
    )

    row = Checkin(
        date=dt.date.today(),
        created_at=dt.datetime.now(),
        energy=checkin.energy,
        sleep=checkin.sleep,
        motivation=checkin.motivation,
        soreness=checkin.soreness,
        minutes=checkin.minutes,
        score=result.score,
        level=result.level,
        format=result.format
    )
    db.add(row)
    db.commit()
    return CheckinOut(score=result.score, level=result.level, format=result.format )


@app.get("/checkin/today")
def get_today_checkin(db: DbSession) -> CheckinOut:
    """Today's latest check-in, used by the Today screen's hero card."""
    row = db.scalars(
        select(Checkin)
        .where(Checkin.date == dt.date.today())
        .order_by(Checkin.id.desc())
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Pas encore de check-in aujourd'hui")
    return CheckinOut(score=row.score, level=row.level, format=row.format)
