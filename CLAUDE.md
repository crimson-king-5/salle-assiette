# Salle & Assiette

Personal workout + nutrition app. Full spec: `SPEC.md` (French).

## Context
- **Explanations to Karim in French.** Code, identifiers and code comments stay in English.
- Learning project: Karim is a **complete beginner in programming** (not just Python). Explain from zero with everyday analogies (variable = labelled box, function = machine, object = form with fields), one idea at a time; show exact code to type when he's stuck, then explain it line by line. Let him write the key logic (algorithms, business rules).
- He edits code in VS Code (earlier in the Claude app editor, where indentation is hard); fixing only his whitespace on request is fine. Changes often go unsaved: if the file on disk hasn't changed, tell him to save (Ctrl+S) before re-checking.
- Frontend: PWA first (per spec). Decided 2026-09-27: finish the PWA for Phase 1, **then** build a native Android app (Kotlin + Jetpack Compose) as a learning project on the same API — Karim wants to learn Android. Health Connect (SPEC Phase 5) is the natural trigger for going native.
- Build phase by phase (SPEC §10). Current: **Phase 1** — FastAPI + DB, check-in / workout endpoints, Today + Workout screens.
- Don't over-design: every step must end with something runnable.

## Layout
- `backend/` — FastAPI app (`app/`), tests (`tests/`), venv in `backend/.venv`.
- `frontend/` — PWA (plain HTML/CSS/JS, no build step), served by FastAPI at `/app/`.
- Dev server: `.claude/launch.json` → port 8001 (8000 is taken by another app), host 0.0.0.0 so the phone can reach it on the LAN.

## Commands (from `backend/`)
- Run tests: `.venv\Scripts\python -m pytest`
