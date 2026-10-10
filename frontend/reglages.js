// Salle & Assiette — "Réglages" screen (SPEC §4.9): goals, weigh-ins, links to the program editor
// (programme.js), file import and data export.
// Weigh-ins are local-first like the protein journal: every change is saved on the phone at once and
// queued in the outbox (app.js): { type: "weight", id, body } = add or correct (PUT /weights/{id}),
// { type: "weight-delete", id } = remove (DELETE). The list is always weightsState (logic.js).
// Goals, program, exercises and import need the PC (online only).
// Uses $, api, store, snapshot, rawSnapshot, offline, enqueue, getOutbox, getSent, getRejected,
// renderPending, syncOutbox, refreshSnapshot, showRejected (app.js), showDialog (workout.js),
// showForm, showToast, formError, storageFullDialog (assiette.js) and the helpers of logic.js.

// KEEP IN SYNC with Objective in backend/app/algorithms.py
const OBJECTIVES = [
  ["recompo", "Recomposition"], ["muscle", "Prise de muscle"], ["perte", "Perte de gras"], ["forme", "Remise en forme"],
];
const objectiveName = (id) => (OBJECTIVES.find(([key]) => key === id) || [id, id])[1];

// Which sub-screen of Réglages is open: "home" or one drawn by programme.js
let rView = "home";

const R_ACTIONS = {};  // data-act of a button -> handler(dataset). programme.js adds its own.


// --- Errors from the API, in French ---

function apiMessage(error, offlineText = "Connecte-toi au PC pour modifier le programme.") {
  if (error instanceof OfflineError) return `📴 ${offlineText}`;
  const detail = error.detail;
  const text = typeof detail === "string" ? detail : "";
  switch (error.status) {
    case 403: return text || "Ce n'est pas modifiable (exercice de la bibliothèque ?).";
    case 404: return "Introuvable : c'est peut-être déjà supprimé. Reviens en arrière et recharge.";
    case 409: return text || "Conflit avec une donnée existante. Réessaie.";
    case 413: return text || "Fichier trop gros (5 Mo maximum).";
    case 415: return text || "Le fichier doit être du JSON.";
    case 422: {
      if (text) return text;
      if (Array.isArray(detail) && detail.length > 0) {
        const where = detail.slice(0, 3).map((d) => (Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(" › ") : "")).filter(Boolean);
        return `Valeurs refusées par le serveur${where.length ? ` (${where.join(" ; ")})` : ""}. Vérifie ce que tu as saisi.`;
      }
      return "Valeurs refusées par le serveur. Vérifie ce que tu as saisi.";
    }
    default:
      return error.status >= 500 ? `Le serveur a eu un souci (${error.status}). Réessaie dans un instant.` : `Erreur (${error.message}).`;
  }
}

// What to tell when the PC is off, per action (default: the program editor's sentence)
const OFFLINE_TEXT = {
  "edit-profile": "Connecte-toi au PC pour changer tes objectifs.",
  export: "Connecte-toi au PC pour exporter tes données.",
};

function reglagesError(error) {
  showToast(apiMessage(error), null, null, 7000);
}


// --- Weigh-ins (offline-first) ---

const currentWeights = () => weightsState(rawSnapshot, getOutbox(), getSent());

function afterWeightChange() {
  renderPending();
  rebuildSnapshot();  // redraws the curve and the list from snapshot + queue
  syncOutbox();  // not awaited: sends it now if the PC is on
}

function saveWeight(id, date, kg) {
  const saved = enqueue("weight", id, { date, kg, created_at: localIso(new Date()) });
  if (!saved) { storageFullDialog(); return false; }
  afterWeightChange();
  return true;
}

function deleteWeight(id) {
  // A delete replaces any waiting add/edit of that weigh-in (one write = nothing half-saved)
  const box = getOutbox().filter((i) => !(i.id === id && (i.type === "weight" || i.type === "weight-delete")));
  box.push({ type: "weight-delete", id, body: null, rev: newRev() });
  if (!store.set("outbox", box)) { storageFullDialog(); return false; }
  afterWeightChange();
  return true;
}

// Add (no entry) or correct (entry = a line of weightsState) a weigh-in.
const WEIGHT_DAYS = 120;  // KEEP IN SYNC with WEIGHT_DAYS in backend/app/snapshot.py: older weigh-ins vanish from the snapshot

async function weightForm(entry) {
  const today = localDate(new Date());
  const oldest = new Date();
  oldest.setDate(oldest.getDate() - WEIGHT_DAYS);
  const minDate = localDate(oldest);
  const last = currentWeights().slice(-1)[0];
  const decimal = (kg) => String(kg).replace(".", ",");
  let draft = { kg: entry ? decimal(entry.kg) : "", date: entry ? entry.date : today };
  let error = "";
  const buttons = [{ label: "Annuler", value: null }];
  if (entry) buttons.push({ label: "Supprimer", value: "delete", className: "danger" });
  buttons.push({ label: "Enregistrer", value: "save", className: "primary" });

  for (;;) {
    const action = await showForm(`
      <h2>${entry ? "Modifier la pesée" : "Nouvelle pesée"}</h2>
      <div class="form">
        <label>Poids (kg)<input id="w-kg" type="text" inputmode="decimal" autocomplete="off" value="${esc(draft.kg)}" placeholder="ex. ${esc(last ? decimal(last.kg) : "86,5")}"></label>
        <label>Date<input id="w-date" type="date" min="${minDate}" max="${today}" value="${esc(draft.date)}"></label>
        <p class="muted hint">Une pesée par jour : la dernière remplace la précédente.</p>
        ${formError(error)}
      </div>`, buttons);
    if (action === "delete") {
      if (deleteWeight(entry.id)) showToast("Pesée supprimée");
      return;
    }
    if (action !== "save") return;
    draft = { kg: $("w-kg").value, date: $("w-date").value };
    const kg = Math.round(Number(draft.kg.replace(",", ".").trim()) * 10) / 10;
    if (!draft.kg.trim() || !(kg >= MIN_KG && kg <= MAX_KG)) error = `Entre un poids en kilos, entre ${MIN_KG} et ${MAX_KG}.`;
    else if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.date) || draft.date > today) error = "Choisis une date qui n'est pas dans le futur.";
    else if (draft.date < minDate) error = `Choisis une date à partir du ${dayText(minDate, true)} : au-delà de ${WEIGHT_DAYS} jours, la pesée ne serait plus affichée.`;
    else {
      // One weigh-in per day: say so before replacing another one
      const other = currentWeights().find((w) => w.date === draft.date && (!entry || w.id !== entry.id));
      if (other && !(await confirmDialog(`Remplacer la pesée du ${dayText(draft.date, true)} ?`,
        `Tu as déjà noté ${kgText(other.kg)} ce jour-là : une seule pesée par jour est gardée.`, "Remplacer"))) continue;
      saveWeight(entry ? entry.id : newId(), draft.date, kg);
      return;
    }
  }
}

const addWeightFlow = () => weightForm(null);

function editWeightFlow(id) {
  const entry = currentWeights().find((w) => w.id === id);
  return entry ? weightForm(entry) : Promise.resolve();
}


// --- Goals (online only: PUT /profile) ---

async function profileFlow() {
  const profile = await api("/profile", { strict: true });
  let draft = {
    objective: profile.objective, sessions: String(profile.sessions_goal),
    protein: String(profile.protein_goal_g), target: String(profile.target_weight_kg).replace(".", ","),
  };
  let error = "";
  const weight = profile.latest_weight_kg;
  const autoText = (objective) => (weight
    ? `Calcul auto : ${autoProteinGoal(weight, objective)} g (${NUMBER.format(PROTEIN_PER_KG[objective])} g × ${NUMBER.format(weight)} kg)`
    : "Calcul auto dès ta première pesée");

  for (;;) {
    const shown = showForm(`
      <h2>Mes objectifs</h2>
      <div class="form">
        <label>Objectif<select id="pf-objective">${OBJECTIVES.map(([key, name]) =>
          `<option value="${esc(key)}"${key === draft.objective ? " selected" : ""}>${esc(name)}</option>`).join("")}</select></label>
        <p class="muted hint" id="pf-hint">${esc(profile.kcal_hints[draft.objective] || "")}</p>
        <label>Séances par semaine<input id="pf-sessions" type="number" inputmode="numeric" min="1" max="7" value="${esc(draft.sessions)}"></label>
        <label>Protéines par jour (g)<input id="pf-protein" type="number" inputmode="numeric" min="60" max="300" value="${esc(draft.protein)}"></label>
        <button type="button" class="ghost small-ghost" id="pf-auto"${weight ? "" : " disabled"}>${esc(autoText(draft.objective))}</button>
        <label>Poids visé (kg)<input id="pf-target" type="text" inputmode="decimal" autocomplete="off" value="${esc(draft.target)}"></label>
        ${formError(error)}
      </div>`,
      [{ label: "Annuler", value: null }, { label: "Enregistrer", value: "save", className: "primary" }],
    );
    const body = $("dialog-body");
    body.onchange = (event) => {
      if (event.target.id !== "pf-objective") return;
      const objective = event.target.value;
      $("pf-hint").textContent = profile.kcal_hints[objective] || "";
      $("pf-auto").textContent = autoText(objective);
    };
    body.onclick = (event) => {
      if (!event.target.closest("#pf-auto") || !weight) return;
      $("pf-protein").value = String(autoProteinGoal(weight, $("pf-objective").value));
    };
    const action = await shown;
    body.onchange = null;
    body.onclick = null;
    if (action !== "save") return false;

    draft = {
      objective: $("pf-objective").value, sessions: $("pf-sessions").value,
      protein: $("pf-protein").value, target: $("pf-target").value,
    };
    const sessions = Math.round(Number(draft.sessions));
    const protein = Math.round(Number(draft.protein));
    const target = Math.round(Number(draft.target.replace(",", ".")) * 10) / 10;
    if (!(sessions >= 1 && sessions <= 7)) error = "Entre un nombre de séances par semaine entre 1 et 7.";
    else if (!(protein >= 60 && protein <= 300)) error = "Entre un objectif de protéines entre 60 et 300 g.";
    else if (!draft.target.trim() || !(target >= MIN_KG && target <= MAX_KG)) error = `Entre un poids visé entre ${MIN_KG} et ${MAX_KG} kg.`;
    else {
      try {
        await api("/profile", {
          method: "PUT", strict: true,
          body: JSON.stringify({ objective: draft.objective, sessions_goal: sessions, protein_goal_g: protein, target_weight_kg: target }),
        });
        await refreshSnapshot();
        showToast("Objectifs enregistrés ✓");
        return true;
      } catch (e) {
        error = apiMessage(e, "Connecte-toi au PC pour changer tes objectifs.");
      }
    }
  }
}


// --- Export ---

async function exportData() {
  const data = await api("/export", { strict: true, timeout: 20000 });
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `salle-assiette-${localDate(new Date())}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
  showToast("Export téléchargé ✓");
}


// --- The home view ---

const weekdayDay = (dateStr) => parseDay(dateStr).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
const WEIGHT_ROWS = 8;

function drawHome() {
  const snap = snapshot;
  const view = $("r-view");
  $("r-offline").hidden = !(offline && snap);
  if (offline && snap) $("r-offline").textContent = "📴 Hors ligne · tes pesées marchent, le reste attend le PC.";

  // An old cached snapshot may lack some fields: show a dash rather than "undefined" or "NaN"
  const known = (value) => value != null && Number.isFinite(Number(value));
  const goals = snap ? `
    <ul class="rows">
      <li><span class="jr-label">Objectif</span><span class="jr-val">${snap.objective ? esc(objectiveName(snap.objective)) : "—"}</span></li>
      <li><span class="jr-label">Séances par semaine</span><span class="jr-val">${known(snap.week_goal) ? Number(snap.week_goal) : "—"}</span></li>
      <li><span class="jr-label">Protéines par jour</span><span class="jr-val protein">${known(snap.protein_goal_g) ? `${Number(snap.protein_goal_g)} g` : "—"}</span></li>
      <li><span class="jr-label">Poids visé</span><span class="jr-val">${known(snap.target_weight_kg) ? esc(kgText(snap.target_weight_kg)) : "—"}</span></li>
    </ul>
    ${snap.kcal_hint ? `<p class="muted">Repère : ${esc(snap.kcal_hint)}. Approche 80/20.</p>` : ""}`
    : `<p class="muted">Ouvre l'app une première fois avec le PC allumé pour voir tes objectifs.</p>`;

  const weights = [...(snap ? snap.weights : [])].reverse();
  const weightRows = weights.slice(0, WEIGHT_ROWS).map((w) => `
    <li><button type="button" class="journal-row" data-act="edit-weight" data-id="${esc(w.id)}" aria-label="Modifier la pesée du ${esc(weekdayDay(w.date))} : ${esc(kgText(w.kg))}">
      <span class="jr-main"><span class="jr-label">${esc(weekdayDay(w.date))}</span></span>
      <span class="jr-val">${esc(kgText(w.kg))}</span>
    </button></li>`).join("");

  const sessions = snap ? snap.sessions : [];
  const rotation = sessions.map((s, i) => `
    <li><span class="jr-label">${i + 1}. ${esc(s.name)} <small>${esc(s.focus)}</small></span>
      ${isStartable(s) ? `<span class="jr-sub">${Number(s.formats.complete.length)} exos</span>` : `<span class="badge">vide</span>`}</li>`).join("");

  view.innerHTML = `
    <section class="card" aria-labelledby="r-goals-title">
      <h2 id="r-goals-title">Objectifs</h2>
      ${goals}
      <button type="button" class="wide" data-act="edit-profile">Modifier mes objectifs</button>
    </section>

    <section class="card" aria-labelledby="r-weight-title">
      <h2 id="r-weight-title">Pesées</h2>
      ${weights.length === 0
        ? `<p class="muted">Pas encore de pesée.</p>`
        : `<p class="muted">Tape une pesée pour la corriger ou la supprimer.</p><ul class="journal">${weightRows}</ul>
           ${weights.length > WEIGHT_ROWS ? `<p class="muted">… et ${weights.length - WEIGHT_ROWS} plus anciennes (visibles sur la courbe de Progrès).</p>` : ""}`}
      <button type="button" class="wide" data-act="add-weight">Ajouter une pesée</button>
    </section>

    <section class="card" aria-labelledby="r-program-title">
      <h2 id="r-program-title">Programme</h2>
      ${sessions.length === 0 ? "" : `<p class="muted">Ta rotation, dans l'ordre.</p><ul class="rows">${rotation}</ul>`}
      ${offline ? `<p class="muted offline-hint">📴 Connecte-toi au PC pour modifier le programme.</p>` : ""}
      <button type="button" class="wide" data-act="program">Modifier le programme</button>
      <button type="button" class="wide" data-act="exercises">Bibliothèque d'exercices</button>
    </section>

    <section class="card" aria-labelledby="r-data-title">
      <h2 id="r-data-title">Mes données</h2>
      <p class="muted">Tout ce que l'app a gardé, en un fichier JSON : séances, pesées, protéines, programme.</p>
      <button type="button" class="wide" data-act="export">Exporter mes données</button>
      <button type="button" class="wide" data-act="import">Importer depuis le prototype</button>
    </section>

    <section class="card" aria-labelledby="r-about-title">
      <h2 id="r-about-title">À propos</h2>
      ${aboutLines(snap)}
    </section>`;
}

function aboutLines(snap) {
  const waiting = getOutbox().length;
  const refused = getRejected().length;
  const when = snap
    ? new Date(snap.generated_at).toLocaleString("fr-FR", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })
    : null;
  return `<ul class="rows">
      <li><span class="jr-label">PC</span><span class="jr-sub">${offline ? "injoignable" : "connecté"}</span></li>
      <li><span class="jr-label">En attente de synchro</span><span class="jr-sub">${waiting === 0 ? "rien, tout est à jour" : String(waiting)}</span></li>
      <li><span class="jr-label">Dernière copie du PC</span><span class="jr-sub">${when ? esc(when) : "aucune"}</span></li>
    </ul>
    ${refused > 0 ? `<button type="button" class="wide" data-act="rejected">${refused} élément${refused > 1 ? "s" : ""} refusé${refused > 1 ? "s" : ""} par le serveur : voir</button>` : ""}`;
}

function showReglagesHome() {
  rView = "home";
  drawHome();
  window.scrollTo(0, 0);
}

// Called when the snapshot changes: only the home view is redrawn (an open editor keeps its draft)
function renderReglages() {
  if (rView === "home") drawHome();
}


// --- Wiring ---

function setupReglages() {
  Object.assign(R_ACTIONS, {
    home: () => showReglagesHome(),
    "add-weight": () => addWeightFlow(),
    "edit-weight": (data) => editWeightFlow(data.id),
    "edit-profile": () => profileFlow(),
    export: () => exportData(),
    rejected: () => showRejected(),
  });
  const NAVIGATION = new Set(["program", "exercises", "import"]);
  const running = new Set();  // actions in flight: a double tap must not export twice or open two dialogs
  $("r-view").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-act]");
    if (!button || button.disabled) return;
    const name = button.dataset.act;
    const action = R_ACTIONS[name];
    const guarded = !NAVIGATION.has(name);  // opening a screen is harmless to repeat
    if (!action || (guarded && running.has(name))) return;
    if (guarded) running.add(name);
    Promise.resolve().then(() => action(button.dataset, button))
      .catch((error) => showToast(apiMessage(error, OFFLINE_TEXT[name] || undefined), null, null, 7000))
      .finally(() => { if (guarded) running.delete(name); });
  });
  drawHome();
}
