// Salle & Assiette — "Aujourd'hui" screen.
// Talks to the FastAPI backend, served from the same address.
// Offline: works from the last snapshot saved on the phone (GET /snapshot).
// Finished workouts and check-ins go into an outbox (localStorage) and are sent
// with idempotent PUTs whenever the PC answers (see syncOutbox).
// Pure helpers (computeReadiness, tonnage, dates, newId, esc) live in logic.js.
// The tab bar and the "Assiette" screen (protein journal, same outbox) live in assiette.js.

const QUESTIONS = [
  { field: "energy", label: "Énergie", emojis: ["🪫", "🙂", "⚡"] },
  { field: "sleep", label: "Sommeil", emojis: ["😵", "🙂", "😴"] },
  { field: "motivation", label: "Motivation", emojis: ["🥱", "🙂", "🔥"] },
  { field: "soreness", label: "Courbatures", emojis: ["😌", "😬", "🤕"] },
];
const VALUES = ANSWER_VALUES; // what each of the 3 buttons is worth (see logic.js / algorithms.py)

const LEVEL_TEXT = { vert: "🟢 Feu vert", orange: "🟠 Feu orange", rouge: "🔴 Feu rouge" };

const answers = {};       // filled as buttons are tapped: { energy: 5, sleep: 3, … }
let todayFormat = null;   // "complete" | "minimale" | "express"
let rawSnapshot = null;   // GET /snapshot exactly as the server sent it (this is what gets saved)
let snapshot = null;      // rawSnapshot + what is still waiting in the outbox (see withPending)
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
    // true when saved. false when storage is full or blocked: callers that must not lose
    // data (outbox, finishing a workout) check it.
    try { localStorage.setItem(`salle.${key}`, JSON.stringify(value)); return true; } catch { return false; }
  },
  remove(key) {
    try { localStorage.removeItem(`salle.${key}`); } catch { /* ignore */ }
  },
};


// --- Talking to the API ---

class OfflineError extends Error {
  constructor() { super("PC injoignable"); }
}

async function api(path, options = {}) {
  // strict: a 404 is an error like any other (default: 404/204 -> null, "nothing there")
  // timeout: ms before giving up (default 5 s; the file import and the export need more)
  const { strict, timeout = 5000, ...init } = options;
  // Give up after a few seconds: when the PC is off, a request can hang for a long time.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  let response;
  try {
    response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      ...init,
    });
  } catch {
    throw new OfflineError();  // no answer at all: network down or PC off
  } finally {
    clearTimeout(timer);
  }
  // 204 = success with no body (DELETE); 404 = "nothing there" unless strict
  if (response.status === 204 || (!strict && response.status === 404)) return null;
  if (!response.ok) {
    const error = new Error(`Erreur serveur (${response.status})`);
    error.status = response.status;
    try { error.detail = (await response.json()).detail; } catch { /* no JSON body */ }  // FastAPI's explanation
    throw error;
  }
  return response.json();
}

function showError(error) {
  $("error").textContent = error instanceof OfflineError
    ? "📴 PC injoignable : cette action a besoin du PC pour l'instant."
    : `Oups : ${error.message}. Le serveur est bien lancé ?`;
  $("error").hidden = false;
}

// Fetch /snapshot and keep it. `fetchedAt` is the phone's clock when the request started: any
// item sent before that moment is inside this snapshot, so its temporary overlay can go.
async function fetchSnapshot() {
  const startedAt = Date.now();
  rawSnapshot = await api("/snapshot");
  store.set("snapshot", rawSnapshot);
  pinProgramMedia(rawSnapshot);  // thumbnails + animations of the program, for when the PC is off (not awaited)
  store.set("sent", getSent().filter((item) => item.sent_at > startedAt));
  offline = false;
}

async function loadSnapshot() {
  try {
    await fetchSnapshot();
  } catch (error) {
    if (!(error instanceof OfflineError)) throw error;
    rawSnapshot = store.get("snapshot");
    offline = true;
  }
  rebuildSnapshot();
  renderBanner();
  if (!offline) {
    await syncOutbox();  // send what is waiting BEFORE showing anything, so the screen is up to date
  }
}

// Re-read the snapshot (tab switch, after an edit in Réglages). Offline: keep the saved copy.
async function refreshSnapshot() {
  try {
    await fetchSnapshot();
  } catch (error) {
    if (!(error instanceof OfflineError)) return;  // a server hiccup: keep what we have
    offline = true;
  }
  rebuildSnapshot();
  renderBanner();
  if (!offline) syncOutbox();
}

function renderBanner() {
  $("offline").hidden = !offline;
  if (offline && snapshot) {
    const when = new Date(snapshot.generated_at).toLocaleString("fr-FR", {
      weekday: "long", hour: "2-digit", minute: "2-digit",
    });
    $("offline").textContent = `📴 Hors ligne · ton programme tel qu'il était ${when}`;
  }
}


// --- Outbox: finished workouts and check-ins waiting to reach the server ---
// Each item: { type, id: <uuid>, body: <document for the PUT> } with type one of
//   "workout" | "checkin" | "protein" (PUT a journal line) | "protein-delete" (DELETE it, body null)
//   | "weight" (PUT a weigh-in) | "weight-delete" (DELETE it, body null)
// Three lists in localStorage:
//   outbox   -> not sent yet (shown as "en attente de synchro", overlaid on the snapshot)
//   sent     -> accepted by the server (2xx) but not yet inside a snapshot we hold; still overlaid
//               (each has sent_at = phone clock in ms) until a snapshot fetched later replaces it
//   rejected -> refused for good by the server (422...): never overlaid, shown for deletion

function getList(key) {
  const list = store.get(key);
  return Array.isArray(list) ? list : [];
}
const getOutbox = () => getList("outbox");
const getSent = () => getList("sent");
const getRejected = () => getList("rejected");

function enqueue(type, id, body) {
  // Same id twice (e.g. a crash between two saves) -> keep only the latest copy
  const box = getOutbox().filter((item) => !(item.type === type && item.id === id));
  box.push({ type, id, body, rev: newRev() });
  const saved = store.set("outbox", box);  // false = phone storage full: caller must not lose the data
  renderPending();
  return saved;
}

function renderPending() {
  const n = getOutbox().length;
  for (const id of ["pending", "a-pending", "p-pending", "r-pending"]) {  // every tab shows it
    $(id).hidden = n === 0;
    $(id).textContent = `☁️ ${n} élément${n > 1 ? "s" : ""} en attente de synchro`;
  }

  const r = getRejected().length;
  $("rejected").hidden = r === 0;
  $("rejected").textContent = `⚠️ ${r} élément${r > 1 ? "s" : ""} refusé${r > 1 ? "s" : ""} par le serveur`;
}

function describeItem(item) {
  const b = item.body || {};
  if (item.type === "workout") return `Séance ${b.name ?? ""} du ${b.date ?? "?"}`;
  if (item.type === "protein") return `Protéines : ${b.label ?? "?"} (${Number(b.grams) || 0} g)`;
  if (item.type === "protein-delete") return "Suppression d'une ligne de protéines";
  if (item.type === "weight") return `Pesée du ${b.date ?? "?"} (${Number(b.kg) || 0} kg)`;
  if (item.type === "weight-delete") return "Suppression d'une pesée";
  return `Check-in du ${b.date ?? "?"}`;
}

async function showRejected() {
  // One "Supprimer" per item; the dialog is redrawn after each deletion
  let again = true;
  while (again) {
    again = false;
    const items = getRejected();
    if (items.length === 0) break;
    const list = items.map((i) => `<li>${esc(describeItem(i))} <small>(erreur ${Number(i.status)})</small> `
      + `<button type="button" class="danger small" data-key="${esc(`${i.type}|${i.id}`)}">Supprimer</button></li>`).join("");
    const shown = showDialog(
      `<h2>Refusé par le serveur</h2><p class="muted">Ces éléments n'ont pas pu être enregistrés et ne le seront jamais.</p><ul>${list}</ul>`,
      [{ label: "Fermer", value: null }],
    );
    $("dialog-body").onclick = (event) => {
      const button = event.target.closest("button[data-key]");
      if (!button) return;
      store.set("rejected", getRejected().filter((i) => `${i.type}|${i.id}` !== button.dataset.key));
      again = true;
      $("dialog").close();  // resolves showDialog; the loop redraws with what is left
    };
    await shown;
    $("dialog-body").onclick = null;
    renderPending();
  }
}

let syncing = false;
let syncAgain = false;

// How each outbox item type is sent: [HTTP method, route]. All of them are idempotent.
const SYNC_ROUTES = {
  workout: ["PUT", "workouts"],
  checkin: ["PUT", "checkins"],
  protein: ["PUT", "protein"],
  "protein-delete": ["DELETE", "protein"],
  weight: ["PUT", "weights"],
  "weight-delete": ["DELETE", "weights"],
};

function moveItem(item, toKey, extra) {
  // Write the destination first, then remove from the outbox: a failure in between
  // only means a harmless re-send later (PUT is idempotent).
  const target = getList(toKey).filter((i) => !(i.type === item.type && i.id === item.id));
  target.push({ ...item, ...extra });
  if (!store.set(toKey, target)) return false;
  // Only the copy that was sent: a newer edit queued meanwhile (other rev) stays for the next pass
  store.set("outbox", withoutSent(getOutbox(), item));
  return true;
}

async function syncOutbox() {
  if (syncing) {
    syncAgain = true;  // something was queued meanwhile: one more pass when this one ends
    return;
  }
  syncing = true;
  try {
    do {
      syncAgain = false;
      let sent = 0;
      let stop = false;
      for (const item of getOutbox()) {
        const route = SYNC_ROUTES[item.type];
        if (!route) continue;  // a type this version doesn't know: leave it alone
        const [method, folder] = route;
        try {
          await api(`/${folder}/${item.id}`, {
            method, body: method === "PUT" ? JSON.stringify(item.body) : undefined, strict: true,
          });
        } catch (error) {
          if (error instanceof OfflineError) { stop = true; break; }  // PC gone again: retry later
          const status = error.status;
          if (status === 404 || status === 405) { stop = true; break; }  // server too old: keep everything
          const transient = [400, 401, 403, 408, 429].includes(status);
          if (status >= 400 && status < 500 && !transient) {
            moveItem(item, "rejected", { status });  // refused for good
          }
          continue;  // 5xx / transient: keep it, try the others
        }
        if (moveItem(item, "sent", { sent_at: Date.now() })) sent++;
      }
      if (sent > 0) {
        try {  // fresh next session, last weights and week count (also drops the "sent" overlays)
          await fetchSnapshot();
        } catch { /* keep overlaying "sent" items until a snapshot arrives */ }
        renderBanner();
      }
      rebuildSnapshot();  // also when items were rejected: they must stop being overlaid
      renderPending();
      if (stop) break;
    } while (syncAgain);
  } finally {
    syncing = false;
    renderPending();
  }
}


// --- Snapshot + outbox: what the phone shows while the PC doesn't know yet ---

function withPending(raw) {
  // The maths is in logic.js (applyPending) so selftest.html can check it
  return applyPending(raw, getOutbox(), getSent(), localDate(new Date()));
}

function rebuildSnapshot() {
  snapshot = withPending(rawSnapshot);
  renderAssiette();  // the protein counter reads the same snapshot + queue
  renderProgress();  // so do the weight curve ...
  renderReglages();  // ... and the settings screen
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

function submitCheckin() {
  // Works with the PC off: compute here, queue it, show the result right away.
  $("submit").disabled = true;
  try {
    const { energy, sleep, motivation, soreness, minutes } = answers;
    const result = computeReadiness(energy, sleep, motivation, soreness, minutes);
    const now = new Date();
    const saved = enqueue("checkin", newId(), {
      energy, sleep, motivation, soreness, minutes,
      date: localDate(now), created_at: localIso(now),
    });
    if (!saved) {
      $("error").textContent = "Stockage du téléphone plein ou bloqué : check-in non enregistré.";
      $("error").hidden = false;
      $("submit").disabled = false;
      return;
    }
    rebuildSnapshot();  // the queued check-in is now "today's check-in"
    showResult(result);
    syncOutbox();  // not awaited: sends it now if the PC is on
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
    .map((s) => `<button type="button" data-id="${esc(s.id)}">${esc(s.name)}${isStartable(s) ? "" : " · vide"}</button>`)
    .join("");

  $("checkin").hidden = true;
  $("result").hidden = false;
  // The one after the last workout done, unless it is empty: then the first one that can be started
  const next = snapshot.sessions.find((s) => s.id === snapshot.next_session_id);
  const fallback = snapshot.sessions.find(isStartable) || snapshot.sessions[0];
  selectSession((next && isStartable(next) ? next : fallback).id);
}

// A session with no exercise can't be started (old saved snapshots have no `startable`: assume yes)
const isStartable = (session) => session.startable !== false;

function selectSession(id) {
  selectedSession = id;
  document.querySelectorAll("#sessions button").forEach((b) => {
    b.classList.toggle("selected", b.dataset.id === id);
  });
  const session = snapshot.sessions.find((s) => s.id === id);
  $("start").disabled = !isStartable(session);
  $("exercises").innerHTML = isStartable(session)
    ? session.formats[todayFormat]
      .map((e) => `<li>${thumbHtml(e.slug, e.name, e.thumb)}<span class="name">${esc(e.name)}</span><span class="sets">${Number(e.sets)} × ${esc(e.reps)}</span></li>`)
      .join("") + (session.formats[todayFormat].some((e) => e.thumb) ? `<li class="credit-row">${creditLine()}</li>` : "")
    : `<li class="empty-note">Séance vide : ajoute des exercices dans Réglages</li>`;
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
  $("resume-btn").addEventListener("click", resumeWorkout);
  window.addEventListener("online", () => { syncOutbox(); });
  $("rejected").addEventListener("click", () => showRejected());
  // App left open past midnight: "today" changed, redraw from scratch
  let shownDay = localDate(new Date());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || localDate(new Date()) === shownDay) return;
    shownDay = localDate(new Date());
    if ($("workout").hidden) { showToday(); renderAssiette(); }
  });
  setupWorkout();
  setupAssiette();
  setupProgress();
  setupCoach();
  setupExercices();
  setupReglages();
  setupProgramme();
  renderPending();
  await showToday();
}

async function showToday() {
  $("error").hidden = true;
  try {
    // A workout left in progress (saved on the phone) comes first: "Reprendre"
    const inProgress = loadLocalWorkout();
    $("resume").hidden = !inProgress;
    if (inProgress) {
      $("resume-name").textContent = inProgress.name;
      $("checkin").hidden = true;
      $("result").hidden = true;
      loadSnapshot().catch(() => { });  // still refresh the saved copy and sync, in the background
      return;
    }

    await loadSnapshot();
    if (!snapshot) {
      $("error").textContent = "📴 PC injoignable, et aucune copie sur ce téléphone. "
        + "Ouvre l'app une première fois avec le PC allumé.";
      $("error").hidden = false;
      return;
    }

    // A check-in saved in yesterday's snapshot doesn't count for today
    const checkinDay = snapshot.checkin_date ?? snapshot.generated_at.slice(0, 10);
    const checkin = checkinDay === localDate(new Date()) ? snapshot.checkin_today : null;
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
