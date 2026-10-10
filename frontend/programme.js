// Salle & Assiette — program editor, exercise library and prototype import (Réglages, SPEC §4.3, §4.4, §3.1).
// ONLINE ONLY: these screens talk to the PC (/sessions, /rotation, /exercises, /import/prototype)
// and say "Connecte-toi au PC pour modifier le programme" when it can't be reached.
// Each sub-screen is drawn into #r-view (reglages.js owns the home view and the click wiring:
// a button with data-act="name" calls R_ACTIONS.name).
// Uses $, api, OfflineError, offline, refreshSnapshot, showToday (app.js), thumbHtml, creditLine, openViewer,
// snapshotDetail, askCoachAbout (exercices.js), showDialog (workout.js),
// showForm, showToast, formError (assiette.js), searchFoods, esc (logic.js), apiMessage,
// R_ACTIONS, rView (reglages.js).

// KEEP IN SYNC with MuscleGroup / Equipment / Focus in backend/app/algorithms.py
const MUSCLES = ["Pectoraux", "Dos", "Épaules", "Biceps", "Triceps", "Avant-bras", "Abdos", "Jambes", "Cardio"];
const EQUIPMENTS = ["Poids libre", "Machine", "Poulie", "Poids du corps", "Smith", "Kettlebell", "Barre EZ", "Élastique", "Autre"];
const FOCUSES = ["Force", "Hypertrophie", "Libre"];
const MAX_IMPORT_BYTES = 5 * 1024 * 1024;  // same limit as the server

let programCache = null;   // GET /sessions: [{ id, name, focus, exercises: [{ slug, name, sets, reps }] }]
let presetCache = null;    // GET /session-templates
let exerciseCache = null;  // GET /exercises (the whole library, sorted by name)
let draft = null;          // the session being edited: { id, name, focus, exercises: [...] }
let draftDirty = false;
const EX_PAGE = 60;  // library rows drawn at a time (1 300+ exercises: never all at once)
const exState = { q: "", group: "", equip: "", shown: EX_PAGE };  // library filters: group = "" | "perso" | "review" | a muscle group

const optionList = (values, selected) =>
  values.map((v) => `<option value="${esc(v)}"${v === selected ? " selected" : ""}>${esc(v)}</option>`).join("");

const backButton = (act = "home", label = "← Réglages", disabled = false) => `<button type="button" class="ghost back" data-act="${act}"${disabled ? " disabled" : ""}>${esc(label)}</button>`;

function setView(name, html, keepScroll = false) {
  rView = name;
  $("r-view").innerHTML = html;
  if (!keepScroll) window.scrollTo(0, 0);
}

// The PC didn't answer (or refused): a card with the reason and a retry button
function showProblem(error, retryAct) {
  setView(rView, `${backButton()}
    <section class="card"><h2>Impossible pour l'instant</h2>
      <p class="muted" role="alert">${esc(apiMessage(error))}</p>
      <button type="button" class="wide" data-act="${esc(retryAct)}">Réessayer</button></section>`);
}

// A change of program/exercise names: the Today screen must redraw from the new snapshot
let refreshTimer = null;
function afterProgramChange() {
  exerciseCache = null;
  clearTimeout(refreshTimer);  // several quick changes (reordering) = one redraw
  refreshTimer = setTimeout(() => { showToday().catch(() => { }); }, 500);
}

async function loadExercises() {
  // 1 300+ exercises (~430 kB): more than the default 5 s on a slow link, but still online-only
  if (!exerciseCache) exerciseCache = await api("/exercises", { strict: true, timeout: 20000 });
  return exerciseCache;
}

const exMeta = (e) => `${e.muscle_group} · ${e.equipment}`;
const badges = (e) => `${e.is_custom ? `<span class="badge perso">perso</span>` : ""}${e.needs_review ? `<span class="badge">à vérifier</span>` : ""}`;

function confirmDialog(title, text, okLabel) {
  return showDialog(
    `<h2>${esc(title)}</h2><p class="muted">${esc(text)}</p>`,
    [{ label: "Annuler", value: false }, { label: okLabel, value: true, className: "danger" }],
  );
}


// --- Rotation: the list of sessions ---

async function openProgram() {
  setView("program", `${backButton()}<p class="muted">Chargement…</p>`);
  try {
    programCache = await api("/sessions", { strict: true });
  } catch (error) {
    if (rView === "program") showProblem(error, "program");
    return;
  }
  if (rView === "program") drawProgram();  // the user may have left meanwhile
}

function drawProgram(keepScroll = false) {
  const last = programCache.length - 1;
  setView("program", `${backButton()}
    <section class="card" aria-labelledby="pr-title">
      <h2 id="pr-title">Rotation</h2>
      <p class="muted">Les séances s'enchaînent dans cet ordre, puis on repart de la première.</p>
      <ol class="sess-list">${programCache.map((s, i) => `
        <li class="sess">
          <p class="sess-head"><span class="jr-label">${i + 1}. ${esc(s.name)}</span>
            ${s.exercises.length === 0 ? `<span class="badge">vide</span>` : ""}
            <small>${esc(s.focus)} · ${s.exercises.length} exercice${s.exercises.length > 1 ? "s" : ""}</small></p>
          <div class="sess-actions">
            <button type="button" data-act="move" data-id="${esc(s.id)}" data-dir="-1" aria-label="Monter ${esc(s.name)}"${i === 0 ? " disabled" : ""}>↑</button>
            <button type="button" data-act="move" data-id="${esc(s.id)}" data-dir="1" aria-label="Descendre ${esc(s.name)}"${i === last ? " disabled" : ""}>↓</button>
            <button type="button" data-act="edit-session" data-id="${esc(s.id)}">Modifier</button>
            <button type="button" class="danger" data-act="delete-session" data-id="${esc(s.id)}" aria-label="Supprimer ${esc(s.name)}">Suppr.</button>
          </div>
        </li>`).join("")}</ol>
      <button type="button" class="primary" data-act="new-session">Nouvelle séance</button>
    </section>`, keepScroll);
}

let moving = false;  // a reorder is on its way: further taps are ignored

async function moveSession(id, dir) {
  if (moving) return;
  moving = true;
  document.querySelectorAll(".sess-actions button").forEach((b) => { b.disabled = true; });
  try {
    await sendMove(id, dir);
  } catch (error) {
    // The server's order is the truth: show it again, then report the problem
    try { programCache = await api("/sessions", { strict: true }); } catch { /* keep what we had */ }
    if (rView === "program") drawProgram(true);
    throw error;
  } finally {
    moving = false;
  }
}

async function sendMove(id, dir) {
  const order = programCache.map((s) => s.id);  // latest known order
  const from = order.indexOf(id);
  const to = from + Number(dir);
  if (from === -1 || to < 0 || to >= order.length) { drawProgram(true); return; }
  [order[from], order[to]] = [order[to], order[from]];
  programCache = await api("/rotation", { method: "PUT", body: JSON.stringify({ order }), strict: true });
  afterProgramChange();  // saved: Today must refresh even if the user already left this screen
  if (rView !== "program") return;
  drawProgram(true);
  const again = document.querySelector(`[data-act="move"][data-id="${CSS.escape(id)}"][data-dir="${dir}"]`)
    || document.querySelector(`[data-act="move"][data-id="${CSS.escape(id)}"]:not(:disabled)`);
  if (again) again.focus();
}

async function deleteSession(id) {
  const session = programCache.find((s) => s.id === id);
  if (!session) return;
  const ok = await confirmDialog(`Supprimer « ${session.name} » ?`, "Tes anciennes séances gardent leur historique et leurs records.", "Supprimer");
  if (!ok) return;
  await api(`/sessions/${encodeURIComponent(id)}`, { method: "DELETE", strict: true });
  programCache = programCache.filter((s) => s.id !== id);
  drawProgram(true);
  showToast("Séance supprimée");
  afterProgramChange();
}

async function newSession() {
  if (!presetCache) presetCache = await api("/session-templates", { strict: true });
  let draftForm = { name: "", focus: "Hypertrophie", template: "vide" };
  let error = "";
  for (;;) {
    const action = await showForm(`
      <h2>Nouvelle séance</h2>
      <div class="form">
        <label>Nom<input id="ns-name" maxlength="80" autocomplete="off" value="${esc(draftForm.name)}" placeholder="ex. Haut du corps C"></label>
        <label>Type<select id="ns-focus">${optionList(FOCUSES, draftForm.focus)}</select></label>
        <label>Départ<select id="ns-template">${presetCache.map((p) =>
          `<option value="${esc(p.id)}"${p.id === draftForm.template ? " selected" : ""}>${esc(p.label)}</option>`).join("")}</select></label>
        ${formError(error)}
      </div>`,
      [{ label: "Annuler", value: null }, { label: "Créer", value: "create", className: "primary" }],
    );
    if (action !== "create") return;
    draftForm = { name: $("ns-name").value.trim(), focus: $("ns-focus").value, template: $("ns-template").value };
    if (!draftForm.name) { error = "Donne un nom à la séance."; continue; }
    try {
      const created = await api("/sessions", { method: "POST", body: JSON.stringify(draftForm), strict: true });
      programCache.push(created);
      afterProgramChange();
      editSession(created.id);
      return;
    } catch (e) {
      error = apiMessage(e);
    }
  }
}


// --- One session: name, type, ordered exercises with sets and reps ---

function editSession(id) {
  const session = programCache.find((s) => s.id === id);
  if (!session) return;
  draft = {
    id, name: session.name, focus: session.focus,
    exercises: session.exercises.map((e) => ({
      slug: e.slug, name: e.name, sets: e.sets, reps: e.reps, thumb: (snapshotDetail(e.slug) || {}).thumb || null,
    })),
  };
  draftDirty = false;
  drawSession();
}

function drawSession(focusSelector = null, error = "") {
  const last = draft.exercises.length - 1;
  setView("session", `${backButton("session-back", "← Rotation")}
    <section class="card" aria-labelledby="se-title">
      <h2 id="se-title">Séance</h2>
      <div class="form">
        <label>Nom<input id="se-name" data-field="name" maxlength="80" autocomplete="off" value="${esc(draft.name)}"></label>
        <label>Type<select id="se-focus" data-field="focus">${optionList(FOCUSES, draft.focus)}</select></label>
        <p class="muted hint">Le repos se règle tout seul : 2 min 30 en Force (exercices lourds), 1 min 30 sinon, 1 min pour l'isolation.</p>
      </div>
    </section>
    <section class="card" aria-labelledby="se-ex-title">
      <h2 id="se-ex-title">Exercices</h2>
      <p class="muted">En version minimale, seuls les 3 premiers sont gardés (2 en express) : mets l'essentiel en haut.</p>
      ${draft.exercises.some((e) => e.thumb) ? creditLine() : ""}
      ${draft.exercises.length === 0
        ? `<p class="muted empty-note-block">Séance vide : ajoute au moins un exercice pour pouvoir la lancer.</p>`
        : `<ol class="ex-list">${draft.exercises.map((e, i) => `
          <li class="ex-row">
            <div class="ex-head">${thumbHtml(e.slug, e.name, e.thumb)}<p class="ex-name">${i + 1}. ${esc(e.name)}</p></div>
            <div class="ex-controls form">
              <label>Séries<input type="number" inputmode="numeric" min="1" max="10" data-field="sets" data-i="${i}" value="${esc(e.sets)}"></label>
              <label>Reps<input type="text" maxlength="20" autocomplete="off" data-field="reps" data-i="${i}" value="${esc(e.reps)}"></label>
              <button type="button" data-act="ex-up" data-i="${i}" aria-label="Monter ${esc(e.name)}"${i === 0 ? " disabled" : ""}>↑</button>
              <button type="button" data-act="ex-down" data-i="${i}" aria-label="Descendre ${esc(e.name)}"${i === last ? " disabled" : ""}>↓</button>
              <button type="button" data-act="ex-del" data-i="${i}" aria-label="Retirer ${esc(e.name)}">✕</button>
            </div>
          </li>`).join("")}</ol>`}
      <button type="button" class="wide" data-act="ex-add">Ajouter un exercice</button>
    </section>
    ${formError(error).replace("<p ", '<p id="se-error" ')}
    <button type="button" class="primary" data-act="session-save">Enregistrer la séance</button>`, focusSelector !== null);
  if (focusSelector) {
    const target = document.querySelector(focusSelector);
    if (target && !target.disabled) target.focus();
  }
}

function onSessionInput(target) {
  const field = target.dataset.field;
  if (!field) return;
  draftDirty = true;
  if (target.dataset.i === undefined) { draft[field] = target.value; return; }
  draft.exercises[Number(target.dataset.i)][field] = target.value;
}

function moveExercise(i, dir) {
  const to = i + dir;
  if (to < 0 || to >= draft.exercises.length) return;
  [draft.exercises[i], draft.exercises[to]] = [draft.exercises[to], draft.exercises[i]];
  draftDirty = true;
  const act = dir < 0 ? "ex-up" : "ex-down";
  drawSession(`[data-act="${act}"][data-i="${to}"]`);
}

async function leaveSession() {
  if (draftDirty) {
    const ok = await confirmDialog("Abandonner les modifications ?", "Ce que tu as changé dans cette séance ne sera pas enregistré.", "Abandonner");
    if (!ok) return;
  }
  draft = null;
  drawProgram();
}

async function saveSession() {
  const name = String(draft.name).trim();
  const lines = draft.exercises.map((e) => ({ slug: e.slug, sets: Math.round(Number(e.sets)), reps: String(e.reps).trim() }));
  let error = "";
  if (!name) error = "Donne un nom à la séance.";
  else if (lines.some((l) => !(l.sets >= 1 && l.sets <= 10))) error = "Chaque exercice a entre 1 et 10 séries.";
  else if (lines.some((l) => !l.reps || l.reps.length > 20)) error = "Indique les répétitions de chaque exercice (ex. 8-12).";
  if (error) { drawSession(null, error); document.getElementById("se-error")?.scrollIntoView({ block: "center" }); return; }

  let saved;
  try {
    saved = await api(`/sessions/${encodeURIComponent(draft.id)}`, {
      method: "PUT", strict: true, body: JSON.stringify({ name, focus: draft.focus, exercises: lines }),
    });
  } catch (e) {
    drawSession(null, apiMessage(e));
    document.getElementById("se-error")?.scrollIntoView({ block: "center" });
    return;
  }
  programCache = programCache.map((s) => (s.id === saved.id ? saved : s));
  draft = null;
  drawProgram();
  showToast("Séance enregistrée ✓");
  afterProgramChange();
}

async function addExerciseToDraft() {
  const exercise = await pickExercise();
  if (!exercise) return;
  draftDirty = true;
  draft.exercises.push({
    slug: exercise.slug, name: exercise.name, thumb: exercise.thumb || null,
    sets: exercise.is_cardio ? 1 : 3, reps: exercise.is_cardio ? "20" : "8-12",  // cardio: minutes
  });
  drawSession(`[data-act="ex-del"][data-i="${draft.exercises.length - 1}"]`);
  // SPEC §7.1: offer the coach's opinion on this addition (online only, and only if the coach is on)
  if (!offline && coachEnabled()) {
    const { slug } = exercise, sessionId = draft.id;
    showToast("Un avis du coach sur cet ajout ?", "Voir", () => exerciseAdviceFlow(slug, sessionId), 8000);
  }
}

const pickFilters = { muscle: "", equip: "" };  // last filters of the "Ajouter" dialog (this app session only)

// Search dialog over the library. Returns the exercise tapped, or null.
async function pickExercise() {
  const SHOWN = 40;
  for (;;) {
    const all = await loadExercises();  // again after "Exercice perso": the library changed
    let picked = null;
    const shown = showDialog(`
      <h2>Ajouter un exercice</h2>
      <div class="form"><input id="xp-query" type="search" autocomplete="off" placeholder="squat, presse, butterfly, poulie…" aria-label="Chercher un exercice">
        <div class="pick-filters">
          <label>Muscle<select id="xp-muscle"><option value="">Tous</option>${optionList(MUSCLES, pickFilters.muscle)}</select></label>
          <label>Matériel<select id="xp-equip"><option value="">Tous</option>${optionList(EQUIPMENTS, pickFilters.equip)}</select></label>
        </div></div>
      <p class="muted pick-note" id="xp-note"></p>
      <ul class="results" id="xp-results"></ul>`,
      [{ label: "Fermer", value: null }, { label: "Exercice perso", value: "create" }],
    );
    const draw = () => {
      const query = $("xp-query").value;
      const muscle = pickFilters.muscle = $("xp-muscle").value;
      const equip = pickFilters.equip = $("xp-equip").value;
      const filtering = query.trim() !== "" || muscle !== "" || equip !== "";
      // Nothing typed, no filter: Karim's own exercises (the 1 300 others are one search away)
      let found;
      if (filtering) {
        found = all.filter((e) => (muscle === "" || e.muscle_group === muscle) && (equip === "" || e.equipment === equip));
        if (query.trim()) found = searchLibrary(query, found);
      } else {
        found = all.filter((e) => !e.is_library);
      }
      const list = found.slice(0, SHOWN);
      $("xp-note").textContent = filtering
        ? (found.length === 0 ? "" : `${found.length} exercice${found.length > 1 ? "s" : ""}`)
        : `Tes exercices. Cherche pour trouver les ${all.length} de la bibliothèque.`;
      $("xp-results").innerHTML = found.length === 0
        ? `<li class="muted">Rien trouvé. Tu peux créer un exercice perso.</li>`
        : (list.some((e) => e.thumb) ? `<li class="credit-row">${creditLine()}</li>` : "")
          + list.map((e) => `<li class="pick-item">${thumbHtml(e.slug, e.name, e.thumb, "peek")}
            <button type="button" data-slug="${esc(e.slug)}"><span>${esc(e.name)} ${badges(e)}</span><small>${esc(exMeta(e))}</small></button></li>`).join("")
          + (found.length > SHOWN ? `<li class="muted">… ${found.length - SHOWN} de plus : précise ta recherche.</li>` : "");
    };
    const body = $("dialog-body");
    body.oninput = draw;
    body.onchange = draw;  // selects: `input` already fires in browsers, `change` is the safety net
    body.onclick = (event) => {
      const peek = event.target.closest("button[data-peek]");
      if (peek) {  // thumbnail: the viewer, with "Choisir cet exercice"
        openViewer(peek.dataset.peek, { name: peek.dataset.name }, { choose: true }).then((action) => {
          if (action !== "choose") return;
          picked = all.find((e) => e.slug === peek.dataset.peek) || null;
          $("dialog").close();
        });
        return;
      }
      const button = event.target.closest("button[data-slug]");
      if (!button) return;
      picked = all.find((e) => e.slug === button.dataset.slug) || null;
      $("dialog").close();
    };
    draw();
    const action = await shown;
    body.oninput = null;
    body.onchange = null;
    body.onclick = null;
    if (action === "create") {
      await exerciseForm(null);
      continue;  // the library changed: search again
    }
    return picked;
  }
}


// --- Exercise library ---

async function openExercises(group = "") {
  Object.assign(exState, { q: "", group, equip: "", shown: EX_PAGE });
  setView("exercises", `${backButton()}<p class="muted">Chargement…</p>`);
  try {
    await loadExercises();
  } catch (error) {
    if (rView === "exercises") showProblem(error, "exercises");
    return;
  }
  if (rView === "exercises") drawExercises();
}

function drawExercises() {
  const all = exerciseCache;
  const review = all.filter((e) => e.needs_review).length;
  setView("exercises", `${backButton()}
    <section class="card" aria-labelledby="ex-title">
      <h2 id="ex-title">Bibliothèque</h2>
      <p class="muted">${all.length} exercices. Ceux de la bibliothèque sont en lecture seule : touche-en un pour voir l'animation et les consignes. Tes exercices perso, tu peux les modifier.</p>
      ${review > 0 ? `<p class="tip">${review} exercice${review > 1 ? "s" : ""} importé${review > 1 ? "s" : ""} à vérifier.
        <button type="button" class="link" data-act="ex-filter-review">Voir</button></p>` : ""}
      <div class="form">
        <label>Chercher<input id="ex-q" type="search" autocomplete="off" value="${esc(exState.q)}" placeholder="squat, presse, butterfly, poulie…"></label>
        <label>Muscle<select id="ex-group">
          <option value="">Tous</option>
          <option value="perso"${exState.group === "perso" ? " selected" : ""}>Perso</option>
          <option value="review"${exState.group === "review" ? " selected" : ""}>À vérifier</option>
          ${MUSCLES.map((m) => `<option value="${esc(m)}"${exState.group === m ? " selected" : ""}>${esc(m)}</option>`).join("")}
        </select></label>
        <label>Matériel<select id="ex-equip">
          <option value="">Tous</option>
          ${EQUIPMENTS.map((m) => `<option value="${esc(m)}"${exState.equip === m ? " selected" : ""}>${esc(m)}</option>`).join("")}
        </select></label>
      </div>
      <button type="button" class="primary" data-act="ex-new">Créer un exercice perso</button>
      ${creditLine()}
      <p class="muted ex-count" id="ex-count" role="status"></p>
      <ul class="rows ex-library" id="ex-list"></ul>
      <button type="button" class="wide" id="ex-more" data-act="ex-more" hidden></button>
    </section>`);
  drawExerciseList();
}

// The rows are drawn EX_PAGE at a time ("Afficher la suite"): the library stays fast with 1 300+ entries
function drawExerciseList() {
  const { group, equip } = exState;
  let items = exerciseCache.filter((e) => (
    (group === "" ? true : group === "perso" ? e.is_custom : group === "review" ? e.needs_review : e.muscle_group === group)
    && (equip === "" || e.equipment === equip)));
  if (exState.q.trim()) items = searchLibrary(exState.q, items);
  const list = items.slice(0, exState.shown);
  $("ex-count").textContent = items.length === 0 ? "" : `${items.length} exercice${items.length > 1 ? "s" : ""}`;
  $("ex-list").innerHTML = items.length === 0
    ? `<li class="muted">Aucun exercice ne correspond.</li>`
    : list.map((e) => `<li class="ex-item">${thumbHtml(e.slug, e.name, e.thumb)}
        <button type="button" class="journal-row" data-act="ex-open" data-slug="${esc(e.slug)}">
          <span class="jr-main"><span class="jr-label">${esc(e.name)} ${badges(e)}</span><small>${esc(exMeta(e))}</small></span>
          <span class="jr-sub" aria-hidden="true">›</span></button></li>`).join("");
  const more = $("ex-more");
  const left = items.length - list.length;
  more.hidden = left <= 0;
  more.textContent = `Afficher la suite (${left} restant${left > 1 ? "s" : ""})`;
}

async function openExercise(slug) {
  const exercise = (await loadExercises()).find((e) => e.slug === slug);
  if (!exercise) return;
  if (!exercise.is_custom) {
    // Library / seeded exercise: read-only, so the viewer (animation, muscles, steps) is the whole story
    const action = await openViewer(exercise.slug, { name: exercise.name }, { coach: !offline && coachEnabled() });
    if (action === "coach") askCoachAbout(exercise.slug, exercise.name);
    return;
  }
  await exerciseForm(exercise);
}

// Create (no exercise) or edit/delete (a custom exercise). Returns true when something changed.
async function exerciseForm(existing) {
  let d = existing
    ? { name: existing.name, muscle: existing.muscle_group, equipment: existing.equipment, isolation: existing.is_isolation,
        lower: existing.is_lower, cardio: existing.is_cardio, unit: existing.cardio_unit || "" }
    : { name: "", muscle: "Pectoraux", equipment: "Poids libre", isolation: false, lower: false, cardio: false, unit: "" };
  let error = "";
  const buttons = [{ label: "Annuler", value: null }];
  if (existing) buttons.push({ label: "Supprimer", value: "delete", className: "danger" });
  buttons.push({ label: "Enregistrer", value: "save", className: "primary" });

  for (;;) {
    const shown = showForm(`
      <h2>${existing ? "Modifier l'exercice" : "Exercice perso"}</h2>
      ${existing && existing.needs_review ? `<p class="tip">Importé avec des valeurs devinées : vérifie le groupe et le matériel, puis enregistre pour retirer « à vérifier ».</p>` : ""}
      <div class="form">
        <label>Nom<input id="xf-name" maxlength="120" autocomplete="off" value="${esc(d.name)}" placeholder="ex. Curl pupitre"></label>
        <label>Groupe musculaire<select id="xf-muscle">${optionList(MUSCLES, d.muscle)}</select></label>
        <label>Matériel<select id="xf-equipment">${optionList(EQUIPMENTS, d.equipment)}</select></label>
        <label class="check-row"><input type="checkbox" id="xf-isolation"${d.isolation ? " checked" : ""}><span>Isolation (une seule articulation)</span></label>
        <label class="check-row"><input type="checkbox" id="xf-lower"${d.lower ? " checked" : ""}><span>Bas du corps</span></label>
        <label class="check-row"><input type="checkbox" id="xf-cardio"${d.cardio || d.muscle === "Cardio" ? " checked" : ""}><span>Cardio (durée en minutes)</span></label>
        <label id="xf-unit-label"${d.cardio || d.muscle === "Cardio" ? "" : " hidden"}>Unité du cardio<input id="xf-unit" maxlength="20" autocomplete="off" value="${esc(d.unit)}" placeholder="ex. min"></label>
        ${formError(error)}
      </div>`, buttons);
    const body = $("dialog-body");
    body.onchange = () => {
      if ($("xf-muscle").value === "Cardio") $("xf-cardio").checked = true;
      $("xf-unit-label").hidden = !$("xf-cardio").checked;
    };
    const action = await shown;
    const read = () => ({
      name: $("xf-name")?.value ?? d.name, muscle: $("xf-muscle")?.value ?? d.muscle, equipment: $("xf-equipment")?.value ?? d.equipment,
      isolation: $("xf-isolation")?.checked ?? false, lower: $("xf-lower")?.checked ?? false,
      cardio: $("xf-cardio")?.checked ?? false, unit: $("xf-unit")?.value ?? "",
    });
    if (action !== "save" && action !== "delete") { body.onchange = null; return false; }
    d = read();
    body.onchange = null;

    if (action === "delete") {
      const ok = await confirmDialog(`Supprimer « ${existing.name} » ?`, "Possible seulement si aucune séance ni aucun historique ne l'utilise.", "Supprimer");
      if (!ok) continue;
      try {
        await api(`/exercises/${encodeURIComponent(existing.slug)}`, { method: "DELETE", strict: true });
      } catch (e) {
        error = apiMessage(e);
        continue;
      }
      exerciseCache = null;
      showToast("Exercice supprimé");
      afterExerciseChange();
      return true;
    }

    const name = d.name.trim();
    if (!name) { error = "Donne un nom à l'exercice."; continue; }
    const payload = {
      name, muscle_group: d.muscle, equipment: d.equipment, is_isolation: d.isolation, is_lower: d.lower,
      is_cardio: d.cardio || d.muscle === "Cardio",
    };
    payload.cardio_unit = payload.is_cardio ? d.unit.trim() || null : null;
    try {
      if (existing) await api(`/exercises/${encodeURIComponent(existing.slug)}`, { method: "PUT", strict: true, body: JSON.stringify(payload) });
      else await api("/exercises", { method: "POST", strict: true, body: JSON.stringify(payload) });
    } catch (e) {
      error = apiMessage(e);
      continue;
    }
    exerciseCache = null;
    showToast(existing ? "Exercice enregistré ✓" : "Exercice créé ✓");
    afterExerciseChange();
    return true;
  }
}

// Redraw whichever screen shows exercises, and refresh Today (names may have changed)
async function afterExerciseChange() {
  afterProgramChange();
  try {
    await loadExercises();
    if (rView === "exercises") drawExercises();
    if (rView === "import") drawImport();
  } catch { /* the next view reloads it */ }
}


// --- Prototype import ---

const imp = { file: "", preview: null, result: null, error: "", busy: false, text: null, gen: 0 };  // gen: a newer action makes older answers stale

function openImport() {
  imp.gen++;  // any request still on its way is now stale
  Object.assign(imp, { file: "", preview: null, result: null, error: "", busy: false, text: null });
  drawImport();
}

function importErrorText(error) {
  if (error instanceof OfflineError) return "📴 Connecte-toi au PC pour importer.";
  if (error.status === 422 && Array.isArray(error.detail)) {
    const lines = error.detail.slice(0, 3).map((d) => `${(d.loc || []).filter((x) => x !== "body").join(" › ")} : ${d.msg}`);
    return `Le fichier n'a pas le bon format. ${lines.join(" ; ")}`;
  }
  return apiMessage(error);
}

function importRows(r) {
  const rows = [
    ["Séances du programme", `${r.sessions_created} créée${r.sessions_created > 1 ? "s" : ""} · ${r.sessions_updated} mise${r.sessions_updated > 1 ? "s" : ""} à jour`],
    ["Objectifs", r.goals_updated ? "mis à jour" : "inchangés"],
    ["Exercices perso créés", String(r.exercises_created.length)],
    ["Exercices à vérifier", String(r.exercises_to_review.length)],
    ["Séances faites", `${r.workouts_created} créée${r.workouts_created > 1 ? "s" : ""} · ${r.workouts_updated} mise${r.workouts_updated > 1 ? "s" : ""} à jour`],
    ["Pesées", `${r.weights_created} créée${r.weights_created > 1 ? "s" : ""} · ${r.weights_updated} mise${r.weights_updated > 1 ? "s" : ""} à jour · ${r.weights_skipped} ignorée${r.weights_skipped > 1 ? "s" : ""}`],
    ["Protéines", `${r.protein_created} créée${r.protein_created > 1 ? "s" : ""} · ${r.protein_updated} mise${r.protein_updated > 1 ? "s" : ""} à jour`],
    ["Ignorés (supprimés dans l'app depuis)", String(r.skipped_deleted)],
  ];
  return `<ul class="rows">${rows.map(([label, value]) => `<li><span class="jr-label">${esc(label)}</span><span class="jr-sub">${esc(value)}</span></li>`).join("")}</ul>
    ${r.weights_skipped > 0 ? `<p class="muted hint">Pesées ignorées : une pesée plus récente existe déjà ce jour-là.</p>` : ""}`;
}

function reviewList() {
  // Exercises still flagged: the import answer, minus those already fixed (exerciseCache is fresh after a fix)
  const flagged = new Set(exerciseCache ? exerciseCache.filter((e) => e.needs_review).map((e) => e.slug) : imp.result.exercises_to_review.map((e) => e.slug));
  const todo = imp.result.exercises_to_review.filter((e) => flagged.has(e.slug));
  if (imp.result.exercises_to_review.length === 0) return "";
  return `<h3 class="shop-title">Exercices à vérifier</h3>
    <p class="muted">Leur groupe et leur matériel ont été devinés d'après le nom. Vérifie-les un par un.</p>
    ${todo.length === 0 ? `<p class="muted">Tout est vérifié ✓</p>`
      : `<ul class="rows">${todo.map((e) => `<li><span class="jr-main"><span class="jr-label">${esc(e.name)}</span><small>${esc(e.muscle_group)} · ${esc(e.equipment)}</small></span>
          <button type="button" class="small" data-act="import-edit" data-slug="${esc(e.slug)}" aria-label="Vérifier ${esc(e.name)}">Vérifier</button></li>`).join("")}</ul>`}`;
}

function drawImport() {
  const picker = `
    <label class="file-btn">
      <input type="file" id="im-file" accept=".json,application/json"${imp.busy ? " disabled" : ""}>
      <span>${imp.preview || imp.result ? "Choisir un autre fichier" : "Choisir le fichier JSON"}</span>
    </label>`;
  let body;
  if (imp.result) {
    body = `<section class="card" aria-labelledby="im-title"><h2 id="im-title">Import terminé ✓</h2>
        <p class="muted">${esc(imp.file)}</p>${importRows(imp.result)}${reviewList()}</section>`;
  } else if (imp.preview) {
    body = `<section class="card" aria-labelledby="im-title"><h2 id="im-title">Aperçu</h2>
        <p class="muted">${esc(imp.file)} : rien n'est enregistré pour l'instant. Voici ce que l'import ferait.</p>
        ${importRows(imp.preview)}
        <button type="button" class="primary" data-act="import-confirm"${imp.busy ? " disabled" : ""}>${imp.busy ? "Import en cours…" : "Confirmer l'import"}</button>
        <button type="button" class="ghost" data-act="import-cancel"${imp.busy ? " disabled" : ""}>Annuler</button></section>`;
  } else {
    body = "";
  }
  setView("import", `${backButton("home", "← Réglages", imp.busy)}
    <section class="card" aria-labelledby="im-intro">
      <h2 id="im-intro">Importer le prototype</h2>
      <p class="muted">Choisis le fichier JSON exporté par le prototype : séances, pesées, protéines et programme sont repris. Tu verras un aperçu avant que quoi que ce soit soit enregistré.</p>
      <p class="warn-note">L'import est prévu pour une seule fois : réimporter le même fichier annule les corrections faites dans l'app sur les éléments importés.</p>
      ${picker}
      ${imp.busy && !imp.preview ? `<p class="muted" role="status">Analyse du fichier…</p>` : ""}
      ${imp.error ? `<p class="form-error" role="alert">${esc(imp.error)}</p>` : ""}
    </section>
    ${body}`);
}

async function onImportFile(file) {
  if (!file) return;
  const gen = ++imp.gen;
  Object.assign(imp, { file: file.name, preview: null, result: null, error: "", text: null });
  if (file.size > MAX_IMPORT_BYTES) { imp.busy = false; imp.error = "Fichier trop gros (5 Mo maximum)."; drawImport(); return; }
  imp.busy = true;
  drawImport();
  try {
    const text = await file.text();
    if (gen !== imp.gen) return;
    const preview = await api("/import/prototype?dry_run=true", { method: "POST", body: text, strict: true, timeout: 60000 });
    if (gen !== imp.gen) return;
    imp.text = text;  // text and preview are set together: the preview always belongs to this text
    imp.preview = preview;
  } catch (error) {
    if (gen !== imp.gen) return;
    imp.error = importErrorText(error);
  }
  imp.busy = false;
  if (rView === "import") drawImport();
}

async function confirmImport() {
  if (!imp.text || !imp.preview || imp.busy) return;
  const gen = imp.gen;
  imp.busy = true;
  imp.error = "";
  drawImport();
  try {
    const result = await api("/import/prototype", { method: "POST", body: imp.text, strict: true, timeout: 60000 });
    if (gen !== imp.gen) return;
    imp.result = result;
    imp.preview = null;
    imp.text = null;  // the file is done with: don't keep megabytes in memory
    exerciseCache = null;
    await loadExercises().catch(() => { });
    await refreshSnapshot();
    afterProgramChange();
  } catch (error) {
    if (gen !== imp.gen) return;
    imp.error = importErrorText(error);
  }
  imp.busy = false;
  if (rView === "import") drawImport();
}


// --- Wiring ---

function setupProgramme() {
  Object.assign(R_ACTIONS, {
    program: () => openProgram(),
    exercises: () => openExercises(),
    import: () => openImport(),
    move: (data) => moveSession(data.id, data.dir),
    "edit-session": (data) => editSession(data.id),
    "delete-session": (data) => deleteSession(data.id),
    "new-session": () => newSession(),
    "session-back": () => leaveSession(),
    "session-save": () => saveSession(),
    "ex-add": () => addExerciseToDraft(),
    "ex-up": (data) => moveExercise(Number(data.i), -1),
    "ex-down": (data) => moveExercise(Number(data.i), 1),
    "ex-del": (data) => {
      draft.exercises.splice(Number(data.i), 1);
      draftDirty = true;
      drawSession('[data-act="ex-add"]');
    },
    "ex-new": () => exerciseForm(null),  // afterExerciseChange (inside) redraws the list
    "ex-open": (data) => openExercise(data.slug),
    "ex-filter-review": () => { exState.group = "review"; exState.shown = EX_PAGE; drawExercises(); },
    "ex-more": () => { exState.shown += EX_PAGE; drawExerciseList(); },
    "import-confirm": () => confirmImport(),
    "import-cancel": () => openImport(),
    "import-edit": async (data) => {
      const exercise = (await loadExercises()).find((e) => e.slug === data.slug);
      if (exercise) await exerciseForm(exercise);  // afterExerciseChange (inside) redraws this screen
    },
  });

  const view = $("r-view");
  view.addEventListener("input", (event) => {
    if (rView === "session") onSessionInput(event.target);
    if (rView === "exercises" && event.target.id === "ex-q") { exState.q = event.target.value; exState.shown = EX_PAGE; drawExerciseList(); }
  });
  view.addEventListener("change", (event) => {
    if (rView === "session") onSessionInput(event.target);
    if (rView === "exercises" && event.target.id === "ex-group") { exState.group = event.target.value; exState.shown = EX_PAGE; drawExerciseList(); }
    if (rView === "exercises" && event.target.id === "ex-equip") { exState.equip = event.target.value; exState.shown = EX_PAGE; drawExerciseList(); }
    if (rView === "import" && event.target.id === "im-file") {
      onImportFile(event.target.files[0]).catch((error) => showToast(importErrorText(error), null, null, 7000));
      event.target.value = "";  // choosing the same file again must fire "change" again
    }
  });
}
