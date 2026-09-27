// Salle & Assiette — "Aujourd'hui" screen.
// Talks to the FastAPI backend, served from the same address.

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
let sessions = [];        // the rotation, loaded once from GET /sessions

const $ = (id) => document.getElementById(id);


// Turn a format into the sentence shown on the hero card.
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


// --- Talking to the API ---

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Erreur serveur (${response.status})`);
  return response.json();
}

function showError(error) {
  $("error").textContent = `Oups : ${error.message}. Le serveur est bien lancé ?`;
  $("error").hidden = false;
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
    await showResult(result);
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


// --- Hero card ---

async function showResult(result) {
  todayFormat = result.format;
  $("result").dataset.level = result.level;
  $("level").textContent = LEVEL_TEXT[result.level];
  $("format").textContent = formatLabel(result.format);

  if (sessions.length === 0) sessions = await api("/sessions");
  $("sessions").innerHTML = sessions
    .map((s) => `<button type="button" data-id="${s.id}">${s.name}</button>`)
    .join("");

  $("checkin").hidden = true;
  $("result").hidden = false;
  await selectSession(sessions[0].id); // no workout history yet: start of the rotation
}

async function selectSession(id) {
  document.querySelectorAll("#sessions button").forEach((b) => {
    b.classList.toggle("selected", b.dataset.id === id);
  });
  const session = await api(`/sessions/${id}?format=${todayFormat}`);
  $("exercises").innerHTML = session.exercises
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
    if (button) selectSession(button.dataset.id).catch(showError);
  });

  try {
    const today = await api("/checkin/today");
    if (today) await showResult(today);
    else showCheckin();
  } catch (error) {
    showError(error);
  }
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => { }); // needs HTTPS or localhost
}

start();
