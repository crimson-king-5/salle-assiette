// Salle & Assiette — AI coach (SPEC §7): the "Coach" tab (two chat threads), the meal coach in
// Assiette and the advice after adding an exercise in the program editor.
// ONLINE ONLY: everything goes through the PC (/coach/…), which holds the Gemini key. The phone never
// sees the key. The model's text is untrusted: it is always put in the page through esc() (or
// textContent), never as HTML.
// Uses $, api, OfflineError, store, snapshot, offline, refreshSnapshot (app.js), showDialog (workout.js),
// showToast, addProteinLine, deleteProteinLine (assiette.js), esc, cleanLabel (logic.js),
// loadExercises, draft, drawSession, rView, programCache (programme.js).

const COACH_THREADS = {
  training: {
    name: "Entraînement",
    suggestions: ["Que changer dans mon programme ?", "Comment progresser au squat ?", "J'ai 30 min : que faire aujourd'hui ?"],
  },
  nutrition: {
    name: "Nutrition",
    suggestions: ["Comment atteindre mes protéines aujourd'hui ?", "Une idée de collation riche en protéines ?", "Que manger ce soir ?"],
  },
};
const COACH_MAX_CHARS = 2000;  // KEEP IN SYNC with MAX_MESSAGE_CHARS in backend/app/coach.py
const COACH_MEAL_MAX_CHARS = 600;  // ...and MAX_MEAL_CHARS
const COACH_CHAT_TIMEOUT_MS = 90000;  // a turn can chain several model calls
const COACH_OFFLINE_TEXT = "Le coach a besoin du PC allumé.";
const COACH_DISABLED_TEXT = "Le coach est désactivé. Réactive-le dans Réglages.";
const COACH_NOT_CONFIGURED_TEXT = "Coach non configuré : ajoute GEMINI_API_KEY dans backend/.env";

let coachThread = "training";  // restored from the phone's storage in setupCoach (store lives in app.js, loaded later)
let coachMessages = [];     // the thread on screen, as the server sent it
let coachNote = "";         // a problem to show above the input (offline, not configured, error)
let coachBusy = false;
let coachLoaded = null;     // which thread coachMessages belongs to (null = none loaded)

function coachAllowChanges() {
  return store.get("coachAllow") !== false;  // on unless the user switched it off
}

// What to tell for a failed /coach call (the server already writes French messages)
function coachErrorText(error) {
  if (error instanceof OfflineError) return COACH_OFFLINE_TEXT;
  if (typeof error.detail === "string" && error.detail) return error.detail;
  if (error.status === 422) return "Ce message n'est pas accepté. Vérifie-le (non vide, moins de 2 000 caractères).";
  return error.status >= 500 ? "Le coach a eu un souci. Réessaie dans un instant." : `Erreur (${error.message}).`;
}

function coachEnabled() {
  // An old cached snapshot may lack the field: assume on
  return !(snapshot && snapshot.coach_enabled === false);
}


// --- The Coach tab ---

function renderCoach() {
  const tabs = Object.entries(COACH_THREADS).map(([key, t]) =>
    `<button type="button" role="tab" data-thread="${esc(key)}" aria-selected="${key === coachThread}"${key === coachThread ? ' class="selected"' : ""}>${esc(t.name)}</button>`).join("");
  $("c-threads").innerHTML = tabs;

  const blocked = !coachEnabled() ? COACH_DISABLED_TEXT : (offline ? COACH_OFFLINE_TEXT : "");
  const note = blocked || coachNote;
  $("c-note").hidden = !note;
  $("c-note").textContent = note;
  $("c-input").disabled = $("c-send").disabled = coachBusy || Boolean(blocked);
  $("c-clear").hidden = coachMessages.length === 0 || Boolean(blocked);
  $("c-allow").checked = coachAllowChanges();
  drawCoachMessages();
}

function drawCoachMessages() {
  const box = $("c-messages");
  if (coachMessages.length === 0 && !coachBusy) {
    box.innerHTML = `<p class="muted">Pose-moi une question, ou touche une idée :</p>
      <div class="chips">${COACH_THREADS[coachThread].suggestions.map((q, i) =>
        `<button type="button" data-q="${i}">${esc(q)}</button>`).join("")}</div>`;
    return;
  }
  const bubbles = coachMessages.map((m) => {
    const actions = m.actions.length === 0 ? "" : `<ul class="coach-actions">${m.actions.map((a) => `
      <li class="${a.status === "undone" ? "undone" : ""}"><span>${a.status === "undone" ? "↩" : "✓"} ${esc(a.summary)}</span>
        ${a.status === "undone" ? `<small>annulé</small>`
          : a.can_undo ? `<button type="button" class="small" data-undo="${Number(a.id)}">Annuler</button>` : ""}</li>`).join("")}</ul>`;
    return `<div class="bubble ${m.role === "user" ? "me" : "coach-says"}"><p>${esc(m.text)}</p>${actions}</div>`;
  });
  if (coachBusy) bubbles.push(`<div class="bubble coach-says" aria-live="polite"><p class="muted">Le coach réfléchit…</p></div>`);
  box.innerHTML = bubbles.join("");
  window.scrollTo({ top: document.body.scrollHeight });
}

async function loadCoachThread() {
  const thread = coachThread;
  coachNote = "";
  if (!coachEnabled() || offline) { renderCoach(); return; }
  try {
    const [status, history] = await Promise.all([
      api("/coach/status", { strict: true }),
      api(`/coach/history?thread=${thread}`, { strict: true }),
    ]);
    if (thread !== coachThread) return;  // the user switched thread meanwhile
    coachMessages = history.messages;
    coachLoaded = thread;
    if (!status.enabled) coachNote = COACH_DISABLED_TEXT;
    else if (!status.configured) coachNote = COACH_NOT_CONFIGURED_TEXT;
  } catch (error) {
    if (thread !== coachThread) return;
    coachMessages = [];
    coachNote = coachErrorText(error);
  }
  renderCoach();
}

async function sendCoachMessage(text) {
  text = text.trim();
  if (!text || coachBusy) return;
  if (text.length > COACH_MAX_CHARS) { coachNote = `Message trop long (${COACH_MAX_CHARS} caractères maximum).`; renderCoach(); return; }
  const thread = coachThread;
  coachBusy = true;
  coachNote = "";
  coachMessages = [...coachMessages, { id: 0, role: "user", text, created_at: "", actions: [] }];
  renderCoach();
  try {
    const history = await api("/coach/chat", {
      method: "POST", strict: true, timeout: COACH_CHAT_TIMEOUT_MS,
      body: JSON.stringify({ thread, message: text, allow_changes: coachAllowChanges() }),
    });
    $("c-input").value = "";
    if (thread === coachThread) coachMessages = history.messages;
    if (history.messages.some((m) => m.actions.length > 0)) refreshSnapshot().catch(() => { });  // goals / program / protein changed
  } catch (error) {
    coachMessages = coachMessages.slice(0, -1);  // the server forgot the question too
    if (!(error instanceof OfflineError)) $("c-input").value = text;  // let the user retry
    coachNote = coachErrorText(error);
  }
  coachBusy = false;
  if (thread === coachThread) renderCoach();
}

async function undoCoachAction(id) {
  try {
    const history = await api(`/coach/undo/${id}`, { method: "POST", strict: true });
    coachMessages = history.messages;
    renderCoach();
    showToast("Modification annulée ✓");
    refreshSnapshot().catch(() => { });
  } catch (error) {
    showToast(coachErrorText(error), null, null, 7000);
  }
}

async function clearCoachThread() {
  const ok = await showDialog(
    `<h2>Effacer la conversation ?</h2><p class="muted">Les changements déjà faits dans l'app restent.</p>`,
    [{ label: "Annuler", value: false }, { label: "Effacer", value: true, className: "danger" }],
  );
  if (!ok) return;
  try {
    const history = await api(`/coach/history?thread=${coachThread}`, { method: "DELETE", strict: true });
    coachMessages = history.messages;
    renderCoach();
  } catch (error) {
    showToast(coachErrorText(error), null, null, 7000);
  }
}

function switchCoachThread(thread) {
  if (!COACH_THREADS[thread] || thread === coachThread) return;
  coachThread = thread;
  store.set("coachThread", thread);
  coachMessages = [];
  coachLoaded = null;
  renderCoach();
  loadCoachThread();
}

function openCoachTab() {
  renderCoach();
  loadCoachThread();
}


// --- Meal coach (Assiette, SPEC §7.2) ---

let mealAdvice = null;   // { text, advice } of the last answer, or null
let mealBusy = false;
let mealNote = "";

const SCORE_TEXT = { top: "👍 Top", correct: "🙂 Correct", a_ameliorer: "💡 À améliorer" };

function renderMealCoach() {
  const blocked = !coachEnabled() ? COACH_DISABLED_TEXT : (offline ? COACH_OFFLINE_TEXT : "");
  const note = blocked || mealNote;
  $("m-note").hidden = !note;
  $("m-note").textContent = note;
  $("m-ask").disabled = mealBusy || Boolean(blocked);
  $("m-ask").textContent = mealBusy ? "Le coach réfléchit…" : "Demander son avis";
  const box = $("m-result");
  if (!mealAdvice) { box.hidden = true; box.innerHTML = ""; return; }
  const a = mealAdvice.advice;
  box.hidden = false;
  box.innerHTML = `
    <p class="meal-score">${esc(SCORE_TEXT[a.score] || SCORE_TEXT.correct)}</p>
    <p>${esc(a.message)}</p>
    ${a.tip ? `<p class="tip">💡 ${esc(a.tip)}</p>` : ""}
    <p class="meal-estimate">Estimation : <strong>${Number(a.protein_g)} g</strong> de protéines · <strong>${Number(a.kcal)}</strong> kcal</p>
    ${a.rest_of_day ? `<p class="muted">${esc(a.rest_of_day)}</p>` : ""}
    ${a.protein_g > 0 ? `<button type="button" class="wide" id="m-add">Ajouter ${Number(a.protein_g)} g au journal</button>` : ""}`;
}

async function askMealCoach() {
  const text = $("m-text").value.trim();
  if (!text || mealBusy) return;
  mealBusy = true;
  mealNote = "";
  renderMealCoach();
  try {
    const advice = await api("/coach/meal", {
      method: "POST", strict: true, timeout: 45000, body: JSON.stringify({ text }),
    });
    mealAdvice = { text, advice };
  } catch (error) {
    mealNote = coachErrorText(error);
  }
  mealBusy = false;
  renderMealCoach();
}

function addMealEstimate() {
  if (!mealAdvice) return;
  const grams = Math.max(1, Math.min(300, Math.round(mealAdvice.advice.protein_g)));
  // Same local-first flow as every journal line: saved on the phone at once, synced later
  const id = addProteinLine(mealAdvice.text, grams);
  if (!id) return;
  showToast(`+${grams} g ajoutés au journal`, "Annuler", () => deleteProteinLine(id));
  mealAdvice = null;
  $("m-text").value = "";
  renderMealCoach();
}


// --- Advice after adding an exercise (program editor, SPEC §7.1) ---

async function exerciseAdviceFlow(slug, sessionId) {
  let advice;
  try {
    advice = await api("/coach/exercise-advice", {
      method: "POST", strict: true, timeout: 45000, body: JSON.stringify({ slug, session_id: sessionId }),
    });
  } catch (error) {
    showToast(coachErrorText(error), null, null, 7000);
    return;
  }
  const target = advice.move_to ? (programCache || []).find((s) => s.id === advice.move_to) : null;
  const buttons = [{ label: "Garder", value: "keep", className: "primary" }];
  if (advice.swap_for) buttons.unshift({ label: `Remplacer par ${advice.swap_for}`, value: "swap" });
  const action = await showDialog(`
    <h2>Avis du coach</h2>
    <p>${esc(advice.message)}</p>
    ${target ? `<p class="muted">Il serait mieux placé dans ${esc(target.name)}.</p>` : ""}
    ${advice.order_tip ? `<p class="muted">💡 ${esc(advice.order_tip)}</p>` : ""}`, buttons);
  if (action !== "swap" || !draft || rView !== "session") return;
  const replacement = (await loadExercises()).find((e) => e.name === advice.swap_for);
  const index = draft.exercises.findIndex((e) => e.slug === slug);
  if (!replacement || index < 0 || draft.exercises.some((e) => e.slug === replacement.slug)) return;
  draft.exercises[index] = { ...draft.exercises[index], slug: replacement.slug, name: replacement.name, thumb: replacement.thumb || null };
  draftDirty = true;
  drawSession();
}


// --- Wiring ---

function setupCoach() {
  if (store.get("coachThread") === "nutrition") coachThread = "nutrition";
  $("c-threads").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-thread]");
    if (button) switchCoachThread(button.dataset.thread);
  });
  $("c-messages").addEventListener("click", (event) => {
    const chip = event.target.closest("button[data-q]");
    if (chip) { sendCoachMessage(COACH_THREADS[coachThread].suggestions[Number(chip.dataset.q)]); return; }
    const undo = event.target.closest("button[data-undo]");
    if (undo) undoCoachAction(Number(undo.dataset.undo));
  });
  $("c-form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendCoachMessage($("c-input").value);
  });
  $("c-allow").addEventListener("change", (event) => store.set("coachAllow", event.target.checked));
  $("c-clear").addEventListener("click", () => clearCoachThread());

  $("m-form").addEventListener("submit", (event) => {
    event.preventDefault();
    askMealCoach();
  });
  $("m-result").addEventListener("click", (event) => {
    if (event.target.closest("#m-add")) addMealEstimate();
  });
  $("m-text").maxLength = COACH_MEAL_MAX_CHARS;
  renderMealCoach();
}
