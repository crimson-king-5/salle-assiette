# Salle & Assiette

Personal workout + nutrition app. Full spec: `SPEC.md` (French).

## Context
- **Explanations to Karim in French.** Code, identifiers and code comments stay in English.
- Learning project: Karim is a **complete beginner in programming** (not just Python). Explain from zero with everyday analogies (variable = labelled box, function = machine, object = form with fields), one idea at a time; show exact code to type when he's stuck, then explain it line by line. Let him write the key logic (algorithms, business rules).
- He edits code in the Claude app editor, where indentation is hard; fixing only his whitespace on request is fine.
- Frontend: PWA first (per spec). Native Android / Capacitor is a later option.
- Build phase by phase (SPEC §10). Current: **Phase 1** — FastAPI + DB, check-in / workout endpoints, Today + Workout screens.
- Don't over-design: every step must end with something runnable.

## Layout
- `backend/` — FastAPI app (`app/`), tests (`tests/`), venv in `backend/.venv`.

## Commands (from `backend/`)
- Run tests: `.venv\Scripts\python -m pytest`
