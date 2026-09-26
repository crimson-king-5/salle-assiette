from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.algorithms import compute_readiness
from app.schemas import CheckinIn, CheckinOut

app = FastAPI(title="Salle & Assiette")


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    # No frontend yet: send visitors to the interactive API docs.
    return RedirectResponse("/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/checkin")
def create_checkin(checkin: CheckinIn) -> CheckinOut:
    """Morning check-in → traffic light + workout format (SPEC §5.1)."""
    result = compute_readiness(
        energy=checkin.energy,
        sleep=checkin.sleep,
        motivation=checkin.motivation,
        soreness=checkin.soreness,
        minutes=checkin.minutes
    )
    return CheckinOut(score=result.score, level=result.level, format=result.format )
