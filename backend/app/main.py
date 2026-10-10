import datetime as dt
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.algorithms import Format, apply_format, compute_readiness
from app.database import Base, DbSession, engine
from app.models import Checkin, SessionTemplate, to_planned
from app.queries import get_profile, next_session_id, today_checkin
from app.migrations import add_client_id_columns, add_program_columns
from app.schemas import CheckinIn, CheckinOut, CheckinSyncIn, ClientId, SessionOut, SyncOut
from app.seed import backfill_aliases, seed_default_program
from app.protein import router as protein_router
from app.snapshot import router as snapshot_router
from app.workouts import router as workouts_router
from app.weights import router as weights_router
from app.profile import router as profile_router
from app.exercises import router as exercises_router
from app.program import router as program_router
from app.export import router as export_router
from app.prototype_import import router as import_router
from app.coach import router as coach_router

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def init_database(bind: Engine) -> None:
    """Bring a database (new, or an old salle.db) up to date. Safe to run at every startup."""
    Base.metadata.create_all(bind)  # creates missing tables, e.g. user_profile / protein_log
    add_client_id_columns(bind)  # old salle.db files lack the offline-sync columns
    add_program_columns(bind)  # ...and the cardio / custom-exercise / coach columns
    with Session(bind) as db:
        seed_default_program(db)
        backfill_aliases(db)
        get_profile(db)  # makes sure the single settings row exists


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database(engine)
    yield


app = FastAPI(title="Salle & Assiette", lifespan=lifespan)

# The server only listens on 127.0.0.1 (Tailscale forwards to it), so only these Host
# headers are legitimate. Anything else (DNS rebinding, a stray LAN client) gets 400.
# "testserver" is the host name used by pytest's TestClient.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "desktop-m7c8gnc.tail696942.ts.net", "testserver"]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


# Browsers send an Origin header on cross-site writes. A page on another site must not be able to
# change data, even with a "simple" request (text/plain form or fetch, no preflight). Requests
# without Origin (curl, the test client, same-origin GETs) are not browser cross-site requests.
ALLOWED_ORIGINS = {
    "https://desktop-m7c8gnc.tail696942.ts.net", "http://localhost:8001", "http://127.0.0.1:8001",
}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def refuse_foreign_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in SAFE_METHODS and origin is not None and origin not in ALLOWED_ORIGINS:
        return JSONResponse(status_code=403, content={"detail": "Origine non autorisée"})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 without echoing the received values.

    FastAPI's default answer repeats the bad input; if that input holds characters that can't be
    encoded (a lone surrogate), building the answer itself crashes and the client gets a 500.
    """
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


app.include_router(workouts_router)
app.include_router(protein_router)
app.include_router(snapshot_router)
app.include_router(weights_router)
app.include_router(profile_router)
app.include_router(exercises_router)
app.include_router(program_router)
app.include_router(export_router)
app.include_router(import_router)
app.include_router(coach_router)


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


@app.put("/checkins/{client_id}")
def sync_checkin(client_id: ClientId, body: CheckinSyncIn, db: DbSession, response: Response) -> SyncOut:
    """Offline sync: store a check-in made on the phone. Idempotent (same UUID → replaced).

    The traffic light is recomputed here from the 5 answers; the phone's own
    result is never trusted.
    """
    result = compute_readiness(
        energy=body.energy, sleep=body.sleep, motivation=body.motivation,
        soreness=body.soreness, minutes=body.minutes,
    )
    key = str(client_id)
    for attempt in (1, 2):
        row = db.scalars(select(Checkin).where(Checkin.client_id == key)).first()
        created = row is None
        if created:
            row = Checkin(client_id=key)
            db.add(row)
        row.date = body.date
        row.created_at = body.created_at
        row.energy, row.sleep, row.motivation = body.energy, body.sleep, body.motivation
        row.soreness, row.minutes = body.soreness, body.minutes
        row.score, row.level, row.format = result.score, result.level, result.format
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()  # same client_id inserted at once by another request: retry as update
            if not created or attempt == 2:
                raise

    response.status_code = 201 if created else 200
    return SyncOut(id=row.id, created=created)


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
