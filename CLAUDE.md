# Salle & Assiette

Personal workout + nutrition app. Full spec: `SPEC.md` (French).

## Context
- **Explanations to Karim in French.** Code, identifiers and code comments stay in English.
- Learning project: Karim is a **complete beginner in programming** (not just Python). Explain from zero with everyday analogies (variable = labelled box, function = machine, object = form with fields), one idea at a time; show exact code to type when he's stuck, then explain it line by line. Let him write the key logic (algorithms, business rules).
- He edits code in VS Code (earlier in the Claude app editor, where indentation is hard); fixing only his whitespace on request is fine. Changes often go unsaved: if the file on disk hasn't changed, tell him to save (Ctrl+S) before re-checking.
- Frontend: PWA first (per spec). Decided 2026-09-27: finish the PWA for Phase 1, **then** build a native Android app (Kotlin + Jetpack Compose) as a learning project on the same API — Karim wants to learn Android. Health Connect (SPEC Phase 5) is the natural trigger for going native.
- Build phase by phase (SPEC §10). **Phase 1** screens are done (Today + Workout). Current: **offline mode** — Karim won't keep the PC on 24/7, so the PWA must work with the PC off and sync later. Plan (decided 2026-09-27): 1) Tailscale HTTPS + install ✅ 2) cache program/next session/last weights on the phone 3) local-first workout with client UUID + idempotent `PUT /workouts/{id}` sync; Karim ports `tonnage` to JS 4) offline check-in; Karim ports `compute_readiness` to JS. Program editing stays online-only.
- Hosting: the PC is the server, reached through Tailscale. `tailscale serve --bg 8001` → https://desktop-m7c8gnc.tail696942.ts.net (tailnet only, no app login needed). `start-server.ps1` = production run on 127.0.0.1:8001.
- Don't over-design: every step must end with something runnable.

## Layout
- `backend/` — FastAPI app (`app/`), tests (`tests/`), venv in `backend/.venv`.
- `frontend/` — PWA (plain HTML/CSS/JS, no build step), served by FastAPI at `/app/`.
- Dev server: `.claude/launch.json` → port 8001 (8000 is taken by another app), host 0.0.0.0 so the phone can reach it on the LAN.

## Commands (from `backend/`)
- Run tests: `.venv\Scripts\python -m pytest`
