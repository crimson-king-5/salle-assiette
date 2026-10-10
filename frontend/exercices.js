// Salle & Assiette — exercise media: thumbnails, the full-screen viewer (SPEC §4.5) and the offline media cache.
// The animations and steps come from the exercise library (backend/app/library.py). The media belongs to
// Gym visual: it is shown at its own 180×180 size (the viewer only enlarges it to fit the screen),
// always with the attribution below, and is served by the PC at /media/ex/… (never stored in git).
// Uses $, api, OfflineError, store, snapshot, offline (app.js), esc, normalizeText (logic.js),
// showTab (assiette.js), switchCoachThread, coachEnabled (coach.js).

const MEDIA_BASE = "/media/ex";
const ATTRIBUTION = "© Gym visual — gymvisual.com";
const PINNED_CACHE = "salle-media-pinned-v1";  // the program's media: kept until the program changes
const FILE_NAME = /^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/;  // what the server accepts; anything else is not shown

// A media URL, or "" when the file name looks wrong (the server's data is not trusted blindly)
function mediaUrl(kind, file) {
  return typeof file === "string" && FILE_NAME.test(file) ? `${MEDIA_BASE}/${kind}/${encodeURIComponent(file)}` : "";
}

const creditLine = () => `<p class="credit">${esc(ATTRIBUTION)}</p>`;

// A square thumbnail that opens the viewer (a button: ≥ 48 px). Exercises without media get a quiet placeholder.
function thumbHtml(slug, name, file, attr = "view") {
  const src = mediaUrl("images", file);
  if (!src) return `<span class="thumb none" aria-hidden="true"></span>`;
  return `<button type="button" class="thumb" data-${attr}="${esc(slug)}" data-name="${esc(name)}" aria-label="Voir l'exercice : ${esc(name)}">
    <img src="${esc(src)}" alt="" width="48" height="48" loading="lazy" decoding="async"></button>`;
}

// A broken image (offline, not cached yet) becomes a neutral placeholder instead of the browser's broken icon
document.addEventListener("error", (event) => {
  const img = event.target;
  if (!(img instanceof HTMLImageElement) || !img.closest(".thumb, .ex-anim, .viewer-pic")) return;
  img.hidden = true;
  img.parentElement.classList.add("missing");
}, true);


// --- What we know about an exercise: the snapshot (program, works offline) or GET /exercises/{slug} ---

const detailCache = new Map();

function snapshotDetail(slug) {
  const details = snapshot && snapshot.exercise_details;
  return details && Object.prototype.hasOwnProperty.call(details, slug) ? details[slug] : null;
}

// The detail of one exercise, or null when it can't be had (offline and not in the program)
async function exerciseDetail(slug) {
  const known = snapshotDetail(slug) || detailCache.get(slug);
  if (known) return known;
  try {
    const detail = await api(`/exercises/${encodeURIComponent(slug)}`, { strict: true });
    if (detail) detailCache.set(slug, detail);
    return detail;
  } catch {
    return null;
  }
}


// --- The viewer: a full-screen <dialog> (stacks above the other dialogs) ---

let viewerToken = 0;  // a newer opening makes an older, slower one stale

function viewerBody(slug, hint, detail, options) {
  const name = detail ? detail.name : hint.name || "Exercice";
  const gif = detail ? mediaUrl("videos", detail.gif) : "";
  const badges = [];
  if (detail) {
    badges.push(`<span class="badge muscle">${esc(detail.muscle_group)}</span>`);
    badges.push(`<span class="badge equip">${esc(detail.equipment)}</span>`);
    if (detail.target) badges.push(`<span class="badge target">${esc(detail.target)}</span>`);
    for (const m of (detail.secondary || []).slice(0, 4)) badges.push(`<span class="badge second">${esc(m)}</span>`);
  }
  const steps = detail && detail.steps && detail.steps.length
    ? `<ol class="steps">${detail.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>`
    : `<p class="muted">${detail ? "Pas de consignes pour cet exercice." : "Détails indisponibles : connecte-toi au PC pour les voir."}</p>`;
  const actions = [];
  if (options.choose) actions.push(`<button type="button" class="primary" data-viewer="choose">Choisir cet exercice</button>`);
  if (options.coach && detail) actions.push(`<button type="button" class="wide" data-viewer="coach">Demander au coach comment le faire</button>`);
  return `
    <div class="viewer-top"><button type="button" class="viewer-close" data-viewer="close" aria-label="Fermer">✕</button></div>
    <div class="viewer-scroll">
      <h2 id="viewer-title">${esc(name)}</h2>
      <div class="viewer-badges">${badges.join("")}</div>
      <div class="viewer-pic">${gif
        ? `<img src="${esc(gif)}" alt="Animation : ${esc(name)}" width="180" height="180" decoding="async">`
        : `<span class="no-anim">${detail ? "Pas d'animation" : "Animation indisponible hors ligne"}</span>`}</div>
      ${gif ? creditLine() : ""}
      <h3 class="steps-title">Comment faire</h3>
      ${steps}
      ${actions.join("")}
    </div>`;
}

// Opens the viewer. options: { choose: show "Choisir cet exercice", coach: show "Demander au coach" }.
// Resolves with "choose", "coach" or null (closed).
function openViewer(slug, hint = {}, options = {}) {
  const dialog = $("viewer");
  const token = ++viewerToken;
  return new Promise((resolve) => {
    let result = null;
    const draw = (detail) => {
      if (token !== viewerToken) return;
      dialog.innerHTML = viewerBody(slug, hint, detail, options);
      dialog.querySelector(".viewer-close").focus();
    };
    dialog.onclick = (event) => {
      const button = event.target.closest("[data-viewer]");
      if (!button) return;
      if (button.dataset.viewer !== "close") result = button.dataset.viewer;
      dialog.close();
    };
    dialog.onclose = () => { dialog.onclick = null; dialog.innerHTML = ""; resolve(result); };
    const known = snapshotDetail(slug) || detailCache.get(slug);
    dialog.innerHTML = `<div class="viewer-top"><button type="button" class="viewer-close" data-viewer="close" aria-label="Fermer">✕</button></div>
      <div class="viewer-scroll"><h2 id="viewer-title">${esc(hint.name || "Exercice")}</h2>${known ? "" : `<p class="muted" role="status">Chargement…</p>`}</div>`;
    if (!dialog.open) dialog.showModal();
    if (known) draw(known);
    else exerciseDetail(slug).then(draw);
  });
}

// Tap on a thumbnail or animation anywhere (data-view="slug"): view only, and the coach link when it makes sense
function onViewTap(event) {
  const target = event.target.closest("[data-view]");
  if (!target || target.closest("#viewer")) return;
  event.preventDefault();
  const inWorkout = !$("workout").hidden;
  const slug = target.dataset.view;
  openViewer(slug, { name: target.dataset.name }, { coach: !inWorkout && !offline && coachEnabled() })
    .then((action) => { if (action === "coach") askCoachAbout(slug, target.dataset.name); });
}

// "Demander au coach comment le faire": open the Coach tab with the question already typed (not sent)
function askCoachAbout(slug, name) {
  const detail = snapshotDetail(slug) || detailCache.get(slug);
  const label = (detail && detail.name) || name || "cet exercice";
  showTab("coach");
  if (coachThread !== "training") switchCoachThread("training");
  const input = $("c-input");
  input.value = `Comment bien faire l'exercice « ${label} » ? Donne-moi les points d'attention et les erreurs à éviter.`.slice(0, COACH_MAX_CHARS);
  if (!input.disabled) input.focus();
}


// --- Offline: keep the program's thumbnails and animations on the phone ---
// The service worker keeps what was looked at in "salle-media-v1" (300 entries at most). The media of the
// current program also goes into a pinned cache that is never trimmed, so the workout works with the PC off.

let pinnedKey = null;  // the list last pinned in this page, to do the work once

function programMediaUrls(snap) {
  const urls = [];
  const details = (snap && snap.exercise_details) || {};
  for (const detail of Object.values(details)) {
    for (const url of [mediaUrl("images", detail.thumb), mediaUrl("videos", detail.gif)]) if (url) urls.push(url);
  }
  return urls;
}

async function pinProgramMedia(snap) {
  if (!("caches" in window)) return;
  const urls = programMediaUrls(snap);
  const key = urls.join("|");
  if (key === pinnedKey) return;
  pinnedKey = key;
  try {
    const cache = await caches.open(PINNED_CACHE);
    const wanted = new Set(urls.map((u) => new URL(u, location.href).href));
    for (const request of await cache.keys()) {
      if (!wanted.has(request.url)) await cache.delete(request);  // an exercise left the program
    }
    const missing = [];
    for (const url of urls) if (!(await cache.match(url))) missing.push(url);
    let next = 0, failed = false;
    const worker = async () => {
      while (next < missing.length) {
        const url = missing[next++];
        try {
          const response = await fetch(url);
          if (response.ok) await cache.put(url, response);
          else failed = true;
        } catch { failed = true; }  // offline or not there: tried again on the next snapshot
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    if (failed) pinnedKey = "";
  } catch {
    pinnedKey = "";  // cache storage blocked: try again later
  }
}

function setupExercices() {
  document.addEventListener("click", onViewTap);
}
