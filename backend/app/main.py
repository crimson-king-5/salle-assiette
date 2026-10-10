import datetime as dt
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.algorithms import Format, apply_format, compute_readiness
from app.database import Base, DbSession, SessionLocal, engine
from app.models import Checkin, SessionTemplate, to_planned
from app.queries import next_session_id, today_checkin
from app.schemas import CheckinIn, CheckinOut, SessionOut
from app.seed import seed_default_program
from app.snapshot import router as snapshot_router
from app.workouts import router as workouts_router

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
app.include_router(workouts_router)
app.include_router(snapshot_router)


@app.middleware("http")
async def always_check_app_files(request: Request, call_next):
    # Without this, the browser may reuse an old copy of app.js/index.html
    # after an update. "no-cache" = keep a copy, but ask the server first
    # (a cheap "304 Not Modified" when nothing changed).
    response = await call_next(request)
    if request.url.path.startswith("/app/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


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
    row = today_checkin(db)
    if row is None:
        raise HTTPException(status_code=404, detail="Pas encore de check-in aujourd'hui")
    return CheckinOut(score=row.score, level=row.level, format=row.format)


@app.get("/sessions")
def list_sessions(db: DbSession) -> list[SessionOut]:
    """Every session of the rotation, in order, with all their exercises."""
    templates = db.scalars(select(SessionTemplate).order_by(SessionTemplate.position)).all()
    return [
        SessionOut(id=t.id, name=t.name, focus=t.focus, exercises=to_planned(t))
        for t in templates
    ]


@app.get("/sessions/next")
def get_next_session_id(db: DbSession) -> dict:
    """Next session of the rotation: the one after the last finished workout."""
    return {"id": next_session_id(db)}


# Declared after /sessions/next, otherwise "next" would be read as a session id
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
