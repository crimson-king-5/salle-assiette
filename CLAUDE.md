# Salle & Assiette

Personal workout + nutrition app. Full spec: `SPEC.md` (French).

## Context
- **Explanations to Karim in French.** Code, identifiers and code comments stay in English.
- Learning project: Karim is new to Python. Explain Python/FastAPI concepts as they come up; let him write the key logic (algorithms, business rules).
- Frontend: PWA first (per spec). Native Android / Capacitor is a later option.
- Build phase by phase (SPEC §10). Current: **Phase 1** — FastAPI + DB, check-in / workout endpoints, Today + Workout screens.
- Don't over-design: every step must end with something runnable.

## Layout
- `backend/` — FastAPI app (`app/`), tests (`tests/`), venv in `backend/.venv`.

## Commands (from `backend/`)
- Run tests: `.venv\Scripts\python -m pytest`
