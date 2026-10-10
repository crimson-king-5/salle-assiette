"""Settings routes (SPEC §4.9). Online only: no offline queue for settings."""

from fastapi import APIRouter

from app.algorithms import KCAL_HINTS, auto_protein_goal
from app.database import DbSession
from app.models import UserProfile
from app.queries import get_profile, latest_weight
from app.schemas import ProfileOut, ProfileUpdateIn

router = APIRouter(prefix="/profile", tags=["settings"])


def to_profile_out(db: DbSession, profile: UserProfile) -> ProfileOut:
    weight = latest_weight(db)
    return ProfileOut(
        objective=profile.objective,
        sessions_goal=profile.sessions_goal,
        protein_goal_g=profile.protein_goal_g,
        target_weight_kg=profile.target_weight_kg,
        coach_enabled=profile.coach_enabled,
        kcal_hint=KCAL_HINTS.get(profile.objective, ""),
        kcal_hints=KCAL_HINTS,
        latest_weight_kg=weight,
        auto_protein_goal_g=auto_protein_goal(weight, profile.objective) if weight else None,
    )


@router.get("")
def read_profile(db: DbSession) -> ProfileOut:
    return to_profile_out(db, get_profile(db))


@router.put("")
def update_profile(body: ProfileUpdateIn, db: DbSession) -> ProfileOut:
    """Change the settings. Only the fields present in the body are modified."""
    profile = get_profile(db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(profile, field, value)
    db.commit()
    return to_profile_out(db, profile)
