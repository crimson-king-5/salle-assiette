# Salle & Assiette

Personal workout + nutrition app. Full spec: `SPEC.md` (French).

## Context
- **Explanations to Karim in French.** Code, identifiers and code comments stay in English.
- Karim is a beginner in programming. Since 2026-10-10 this project is **no longer a learning exercise**: Claude writes all the code (no TODO(human) hand-offs). Karim will learn on a separate project later. Keep reports short and plain.
- **Model policy (Karim's rule):** implementation work goes to subagents on **Sonnet** (`model: "sonnet"`), one focused task per agent. **Review and security** passes go to subagents on **Opus 5.5** (`model: "opus"`). Fix what the review confirms before committing.
- Frontend: PWA (plain HTML/CSS/JS). A native Android app (Kotlin + Compose) may come later as a separate learning project on the same API; Health Connect (SPEC Phase 5) needs native.
- Offline: Karim won't keep the PC on 24/7, so the PWA must work with the PC off and sync later. Done: Tailscale HTTPS + install, `/snapshot` cached on the phone. Next: local-first workout (client UUID + idempotent `PUT /workouts/{id}`), offline check-in (JS port of `compute_readiness`, kept in sync with the Python one). Program editing stays online-only.
- Hosting: the PC is the server, reached through Tailscale. `tailscale serve --bg 8001` → https://desktop-m7c8gnc.tail696942.ts.net (tailnet only, no app login needed). `start-server.ps1` = production run on 127.0.0.1:8001.
- Don't over-design: every step must end with something runnable and tested.
- Decided 2026-10-10: build the whole spec. Order: offline workout/check-in → nutrition (Assiette) → progress + settings + program editor → AI coach (**Google Gemini API**, chosen 2026-10-10 instead of Anthropic; SPEC §7 still applies via Gemini function calling; Karim puts `GEMINI_API_KEY` in `backend/.env`, never committed, never pasted in chat). The daily Telegram notification was dropped by Karim on 2026-10-10: don't build it.

- Design (chosen 2026-10-10): direction **B "Craie & néon"** — background #0B0D0E, surface #15181A, line #2A2F33, text #F4F6F2, muted #9BA39C, lime accent #C8F031 (actions), orange #FF9F43 (protein); Barlow Condensed 800 uppercase for headings/numbers, Figtree for body; square corners, big tap targets (≥ 48 px). Mockups: scratchpad design canvas, artboards `B_*.dc.html`.
- Prototype import is meant to be one-shot: re-importing the same file reverts edits made in the app to imported rows. The import screen must say so.

- Exercise library (added 2026-10-10): `vendor/exercises-dataset` (git-ignored; data MIT, media © Gym visual, 180×180 only, attribution "© Gym visual — gymvisual.com" wherever shown, never copied into git). `backend/app/library.py` imports its 1,324 exercises at startup (idempotent, keyed on `dataset_id`, validated as untrusted input), links the seeded ones through `SEED_LINKS`, adds French search aliases, and serves the media at `/media/ex/{images|videos}/{file}`. English names, French steps. `GET /exercises` is lean, `GET /exercises/{slug}` has steps/gif/muscles, `/snapshot` carries `exercise_details` for the program only. Frontend: `exercices.js` (thumbnails, full-screen viewer, pinned offline media), media caches `salle-media-v1` (300 max) and `salle-media-pinned-v1` survive app version bumps. Coach: `search_exercises` read-only tool.

## Layout
- `backend/` — FastAPI app (`app/`), tests (`tests/`), venv in `backend/.venv`.
- `frontend/` — PWA (plain HTML/CSS/JS, no build step), served by FastAPI at `/app/`.
- Dev server: `.claude/launch.json` → 127.0.0.1:8001 (8000 is taken by another app). The phone reaches it only through Tailscale; `TrustedHostMiddleware` allows localhost, 127.0.0.1, the ts.net name and `testserver`.
- `backend/salle.db` holds Karim's real data: agents must never write test data into it (tests use in-memory DBs; scratch runs use a copy). Starting the real server runs the startup migrations on it — that's intended.

## Commands (from `backend/`)
- Run tests: `.venv\Scripts\python -m pytest`
