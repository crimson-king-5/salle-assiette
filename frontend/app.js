// Salle & Assiette — "Aujourd'hui" screen.
// Talks to the FastAPI backend, served from the same address.
// Offline: works from the last snapshot saved on the phone (GET /snapshot).

const QUESTIONS = [
  { field: "energy", label: "Énergie", emojis: ["🪫", "🙂", "⚡"] },
  { field: "sleep", label: "Sommeil", emojis: ["😵", "🙂", "😴"] },
  { field: "motivation", label: "Motivation", emojis: ["🥱", "🙂", "🔥"] },
  { field: "soreness", label: "Courbatures", emojis: ["😌", "😬", "🤕"] },
];
const VALUES = [1, 3, 5]; // what each of the 3 buttons is worth (see algorithms.py)

const LEVEL_TEXT = { vert: "🟢 Feu vert", orange: "🟠 Feu orange", rouge: "🔴 Feu rouge" };

const answers = {};       // filled as buttons are tapped: { energy: 5, sleep: 3, … }
let todayFormat = null;   // "complete" | "minimale" | "express"
let snapshot = null;      // everything needed offline, see GET /snapshot
let offline = false;      // true when the PC can't be reached
let selectedSession = null;  // id of the session chosen on the hero card

const $ = (id) => document.getElementById(id);



function formatLabel(format) {
  if (format === "complete") {
    return "Séance complète · ≈ 60 min";
  }
  else if (format === "minimale") {
    return "Version minimale · ≈ 35 min";
  }
  else {
    return "Express · 25 min, l'important c'est d'y aller";
  }

}


// --- Saving on the phone (localStorage can fail: private mode, blocked storage) ---

const store = {
  get(key) {
    try { return JSON.parse(localStorage.getItem(`salle.${key}`)); } catch { return null; }
  },
  set(key, value) {
    try { localStorage.setItem(`salle.${key}`, JSON.stringify(value)); } catch { /* ignore */ }
  },
};


// --- Talking to the API ---

class OfflineError extends Error {
  constructor() { super("PC injoignable"); }
}

async function api(path, options = {}) {
  // Give up after 5 s: when the PC is off, a request can hang for a long time.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  let response;
  try {
    response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      ...options,
    });
  } catch {
    throw new OfflineError();  // no answer at all: network down or PC off
  } finally {
    clearTimeout(timer);
  }
  if (response.status === 404 || response.status === 204) return null;
  if (!response.ok) throw new Error(`Erreur serveur (${response.status})`);
  return response.json();
}

function showError(error) {
  $("error").textContent = error instanceof OfflineError
    ? "📴 PC injoignable : cette action a besoin du PC pour l'instant."
    : `Oups : ${error.message}. Le serveur est bien lancé ?`;
  $("error").hidden = false;
}

function isToday(isoDate) {
  return isoDate && isoDate.slice(0, 10) === new Date().toLocaleDateString("sv");  // "2026-09-27"
}

async function loadSnapshot() {
  try {
    snapshot = await api("/snapshot");
    store.set("snapshot", snapshot);
    offline = false;
  } catch (error) {
    if (!(error instanceof OfflineError)) throw error;
    snapshot = store.get("snapshot");
    offline = true;
  }
  $("offline").hidden = !offline;
  if (offline && snapshot) {
    const when = new Date(snapshot.generated_at).toLocaleString("fr-FR", {
      weekday: "long", hour: "2-digit", minute: "2-digit",
    });
    $("offline").textContent = `📴 Hors ligne · ton programme tel qu'il était ${when}`;
  }
}


// --- Check-in form ---

function buildQuestions() {
  for (const q of QUESTIONS) {
    const buttons = q.emojis
      .map((emoji, i) => `<button type="button" data-value="${VALUES[i]}" aria-label="${q.label} : ${i + 1} sur 3">${emoji}</button>`)
      .join("");
    $("questions").insertAdjacentHTML("beforeend", `
      <div class="question">
        <p class="label">${q.label}</p>
        <div class="choices" data-field="${q.field}">${buttons}</div>
      </div>`);
  }
}

function onChoiceTap(event) {
  const button = event.target.closest(".choices button");
  if (!button) return;
  const group = button.parentElement;
  group.querySelectorAll("button").forEach((b) => b.classList.remove("selected"));
  button.classList.add("selected");
  answers[group.dataset.field] = Number(button.dataset.value);
  $("submit").disabled = Object.keys(answers).length < 5;
}

async function submitCheckin() {
  $("submit").disabled = true;
  try {
    const result = await api("/checkin", { method: "POST", body: JSON.stringify(answers) });
    snapshot.checkin_today = result;  // keep the saved copy up to date
    store.set("snapshot", snapshot);
    showResult(result);
  } catch (error) {
    showError(error);
    $("submit").disabled = false;
  }
}

function showCheckin() {
  for (const key of Object.keys(answers)) delete answers[key];
  document.querySelectorAll(".choices button").forEach((b) => b.classList.remove("selected"));
  $("submit").disabled = true;
  $("result").hidden = true;
  $("checkin").hidden = false;
}


// --- Hero card (reads everything from the snapshot: works offline) ---

function showResult(result) {
  todayFormat = result.format;
  $("result").dataset.level = result.level;
  $("level").textContent = LEVEL_TEXT[result.level];
  $("format").textContent = formatLabel(result.format);

  $("sessions").innerHTML = snapshot.sessions
    .map((s) => `<button type="button" data-id="${s.id}">${s.name}</button>`)
    .join("");

  $("checkin").hidden = true;
  $("result").hidden = false;
  selectSession(snapshot.next_session_id);  // the one after the last workout done
}

function selectSession(id) {
  selectedSession = id;
  document.querySelectorAll("#sessions button").forEach((b) => {
    b.classList.toggle("selected", b.dataset.id === id);
  });
  const session = snapshot.sessions.find((s) => s.id === id);
  $("exercises").innerHTML = session.formats[todayFormat]
    .map((e) => `<li><span class="name">${e.name}</span><span class="sets">${e.sets} × ${e.reps}</span></li>`)
    .join("");
}


// --- Start ---

async function start() {
  $("date").textContent = new Date().toLocaleDateString("fr-FR", {
    weekday: "long", day: "numeric", month: "long",
  });
  buildQuestions();
  $("checkin").addEventListener("click", onChoiceTap);
  $("submit").addEventListener("click", submitCheckin);
  $("redo").addEventListener("click", showCheckin);
  $("sessions").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (button) selectSession(button.dataset.id);
  });
  $("start").addEventListener("click", () => startWorkout(selectedSession));
  $("resume-btn").addEventListener("click", () => resumeWorkout().catch(showError));
  setupWorkout();
  await showToday();
}

async function showToday() {
  $("error").hidden = true;
  try {
    await loadSnapshot();
    if (!snapshot) {
      $("error").textContent = "📴 PC injoignable, et aucune copie sur ce téléphone. "
        + "Ouvre l'app une première fois avec le PC allumé.";
      $("error").hidden = false;
      return;
    }

    // A workout left in progress comes first: "Reprendre"
    const inProgress = offline ? null : await api("/workouts/current");
    $("resume").hidden = !inProgress;
    if (inProgress) {
      $("resume-name").textContent = inProgress.name;
      $("checkin").hidden = true;
      $("result").hidden = true;
      return;
    }

    // A check-in saved in yesterday's snapshot doesn't count for today
    const checkin = isToday(snapshot.generated_at) ? snapshot.checkin_today : null;
    if (checkin) showResult(checkin);
    else showCheckin();
  } catch (error) {
    showError(error);
  }
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => { }); // needs HTTPS or localhost
}

start();
