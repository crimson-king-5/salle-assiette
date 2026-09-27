import datetime as dt
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.algorithms import Format, PlannedExercise, apply_format, compute_readiness
from app.database import Base, DbSession, SessionLocal, engine
from app.models import Checkin, SessionTemplate
from app.schemas import CheckinIn, CheckinOut, SessionOut
from app.seed import seed_default_program

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: create the tables if they don't exist yet,
    # and insert the default program on first run.
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_default_program(db)
    yield


app = FastAPI(title="Salle & Assiette", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/app/")


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


def to_planned(template: SessionTemplate) -> list[PlannedExercise]:
    """DB rows → the plain PlannedExercise objects used by the algorithms."""
    return [
        PlannedExercise(slug=se.exercise_slug, name=se.exercise.name_fr, sets=se.sets, reps=se.reps)
        for se in template.exercises
    ]


@app.get("/sessions")
def list_sessions(db: DbSession) -> list[SessionOut]:
    """Every session of the rotation, in order, with all their exercises."""
    templates = db.scalars(select(SessionTemplate).order_by(SessionTemplate.position)).all()
    return [
        SessionOut(id=t.id, name=t.name, focus=t.focus, exercises=to_planned(t))
        for t in templates
    ]


@app.get("/sessions/{session_id}")
def get_session(session_id: str, db: DbSession, format: Format = "complete") -> SessionOut:
    """One session, shortened to the requested format (e.g. ?format=express)."""
    template = db.get(SessionTemplate, session_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Séance inconnue")
    exercises = apply_format(to_planned(template), format)
    return SessionOut(id=template.id, name=template.name, focus=template.focus, exercises=exercises)


# The phone app (PWA): HTML/CSS/JS files served as-is under /app/.
# Mounted last so it never hides an API route.
app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="app")
