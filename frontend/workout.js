// "Séance" screen (SPEC §4.2): one exercise at a time,
// 1-tap set validation, automatic rest timer, end-of-workout recap.
// Local-first: the workout lives in localStorage ("salle.workout") and every tap is
// saved there at once, no network. Only when it is FINISHED does it go to the outbox
// (see app.js) and get sent to the server with an idempotent PUT.
// Uses $, store, snapshot, enqueue, syncOutbox, showError, todayFormat from app.js
// and tonnage, newId, localDate, localIso, esc from logic.js.
//
// Shape of `workout`:
//   { id, session_id, name, focus, format, date, started_at,
//     exercises: [{ slug, name, target_reps, rest_seconds, last_weight,
//                   sets: [{ weight, reps, done }] }] }

let workout = null;     // the workout in progress
let current = 0;        // index of the exercise on screen
let chronoTimer = null;
let restTimer = null;
let restEndsAt = 0;


// --- Helpers ---

function topOfRange(reps) {
  // "8-12" → 12, "10" → 10
  const parts = String(reps).split("-");
  return Number(parts[parts.length - 1]) || null;
}

function mmss(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function numText(value) {
  // A number (or nothing) as text, safe inside an HTML attribute
  return value == null || !Number.isFinite(Number(value)) ? "" : String(Number(value));
}

function showDialog(html, buttons) {
  // Resolves with the value of the button tapped (null if dismissed with Escape).
  // It resolves from the dialog's "close" event, never before: otherwise a caller that opens
  // the next dialog at once would get that stale event on its new dialog and see it close.
  return new Promise((resolve) => {
    const dialog = $("dialog");
    let result = null;
    $("dialog-body").innerHTML = html;
    $("dialog-actions").innerHTML = "";
    for (const b of buttons) {
      const el = document.createElement("button");
      el.type = "button";
      el.textContent = b.label;
      el.className = b.className || "";
      el.addEventListener("click", () => { result = b.value; dialog.close(); });
      $("dialog-actions").append(el);
    }
    dialog.onclose = () => resolve(result);
    dialog.showModal();
  });
}


// --- The workout saved on the phone ---

function saveWorkout() {
  store.set("workout", workout);
}

function loadLocalWorkout() {
  // The in-progress workout, if any (and if the saved data looks sane)
  const saved = store.get("workout");
  const valid = saved && typeof saved.id === "string" && typeof saved.name === "string"
    && Array.isArray(saved.exercises) && saved.exercises.length > 0
    && saved.exercises.every((e) => e && Array.isArray(e.sets));
  return valid ? saved : null;
}


// --- Open / close workout mode ---

function startWorkout(sessionId) {
  const session = snapshot && snapshot.sessions.find((s) => s.id === sessionId);
  const planned = session && session.formats[todayFormat];
  if (session && !isStartable(session)) {
    $("error").textContent = "Séance vide : ajoute des exercices dans Réglages.";
    $("error").hidden = false;
    return;
  }
  if (!planned) {
    showError(new Error("Séance introuvable sur ce téléphone"));
    return;
  }
  const now = new Date();
  workout = {
    id: newId(),
    session_id: session.id,
    name: session.name,
    focus: session.focus,
    format: todayFormat,
    date: localDate(now),
    started_at: localIso(now),
    exercises: planned.map((p) => ({
      slug: p.slug,
      name: p.name,
      target_reps: p.reps,
      rest_seconds: p.rest_seconds,
      last_weight: snapshot.last_weights[p.slug] ?? null,  // prefill
      sets: Array.from({ length: p.sets }, () => ({ weight: null, reps: null, done: false })),
    })),
  };
  saveWorkout();
  current = 0;
  openWorkout();
}

function resumeWorkout() {
  workout = loadLocalWorkout();
  if (!workout) {
    showToday();
    return;
  }
  // Land on the first exercise that still has sets to do
  const firstUnfinished = workout.exercises.findIndex((e) => e.sets.some((s) => !s.done));
  current = firstUnfinished === -1 ? workout.exercises.length - 1 : firstUnfinished;
  openWorkout();
}

function openWorkout() {
  enterWorkoutMode();  // hides the tabs and the tab bar (assiette.js)
  $("workout").hidden = false;
  $("w-name").textContent = workout.name;
  const startedAt = new Date(workout.started_at).getTime();
  const tick = () => { $("chrono").textContent = mmss((Date.now() - startedAt) / 1000); };
  clearInterval(chronoTimer);
  tick();
  chronoTimer = setInterval(tick, 1000);
  render();
}

function closeWorkout() {
  clearInterval(chronoTimer);
  stopRest();
  $("workout").hidden = true;
  leaveWorkoutMode();  // tab bar and the current tab come back (assiette.js)
  showToday();  // refresh the Today screen (app.js)
}


// --- Drawing the current exercise ---

function render() {
  const ex = workout.exercises[current];
  const last = workout.exercises.length - 1;

  $("segments").innerHTML = workout.exercises
    .map((e, i) => {
      const done = e.sets.every((s) => s.done);
      const cls = i === current ? "current" : done ? "done" : "";
      return `<button type="button" class="${cls}" data-index="${i}" aria-label="Exercice ${i + 1} : ${esc(e.name)}"><span></span></button>`;
    })
    .join("");

  $("ex-name").textContent = ex.name;
  const lastTime = ex.last_weight != null ? ` · Dernière fois : ${ex.last_weight} kg` : "";
  $("ex-info").textContent = `${ex.sets.length} × ${ex.target_reps} reps · repos ${ex.rest_seconds} s${lastTime}`;

  $("sets").innerHTML = ex.sets
    .map((s, i) => {
      const weightHint = numText((i > 0 && ex.sets[i - 1].weight) || ex.last_weight) || "kg";
      return `
      <div class="set-row ${s.done ? "done" : ""}" data-index="${i}">
        <span class="set-num">${i + 1}</span>
        <label><input type="text" inputmode="decimal" data-field="weight" value="${numText(s.weight)}" placeholder="${weightHint}" aria-label="Série ${i + 1}, kilos"><small>kg</small></label>
        <label><input type="text" inputmode="numeric" data-field="reps" value="${numText(s.reps)}" placeholder="${numText(topOfRange(ex.target_reps))}" aria-label="Série ${i + 1}, répétitions"><small>reps</small></label>
        <button type="button" class="check">${s.done ? "✓" : "Valider"}</button>
      </div>`;
    })
    .join("");

  $("prev").disabled = current === 0;
  $("next").textContent = current === last ? "Terminer" : "Suivant";
}


// --- Sets: typing and 1-tap validation ---

function onSetInput(event) {
  // Keep what's typed, so it survives a re-render (and an app restart)
  const input = event.target.closest("input");
  if (!input) return;
  const set = workout.exercises[current].sets[input.closest(".set-row").dataset.index];
  const value = input.value.replace(",", ".").trim();
  const number = Number(value);
  set[input.dataset.field] = value === "" || !Number.isFinite(number) ? null : number;
  saveWorkout();
}

function onSetTap(event) {
  const button = event.target.closest(".check");
  if (!button) return;
  const i = Number(button.closest(".set-row").dataset.index);
  const ex = workout.exercises[current];
  const set = ex.sets[i];

  if (set.done) {
    set.done = false;  // re-tap = un-validate
  } else {
    // Empty validation: last known weight and top of the rep range (SPEC §4.2)
    set.weight = set.weight ?? (i > 0 ? ex.sets[i - 1].weight : null) ?? ex.last_weight ?? null;
    set.reps = set.reps ?? topOfRange(ex.target_reps);
    set.done = true;
    const next = ex.sets[i + 1];
    if (next && !next.done && next.weight == null) next.weight = set.weight;  // carry the weight over
  }
  saveWorkout();
  render();
  if (set.done) startRest(ex.rest_seconds);
}


// --- Rest timer ---

function startRest(seconds) {
  stopRest();
  restEndsAt = Date.now() + seconds * 1000;
  $("rest").hidden = false;
  const tick = () => {
    const left = (restEndsAt - Date.now()) / 1000;
    $("rest-time").textContent = mmss(left);
    if (left <= 0) {
      stopRest();
      if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
    }
  };
  tick();
  restTimer = setInterval(tick, 250);
}

function stopRest() {
  clearInterval(restTimer);
  $("rest").hidden = true;
}


// --- Navigation, finish, abandon ---

function goTo(index) {
  current = Math.max(0, Math.min(index, workout.exercises.length - 1));
  render();
  window.scrollTo(0, 0);
}

function cleanSet(s) {
  // Same limits as the server (0-500 kg, 0-100 reps): a refused workout would never sync
  const weight = s.weight != null && Number.isFinite(s.weight) ? Math.min(500, Math.max(0, s.weight)) : null;
  const reps = s.reps != null && Number.isFinite(s.reps) ? Math.min(100, Math.max(0, Math.round(s.reps))) : null;
  return { weight, reps, done: s.done === true };
}

async function finishWorkout() {
  const left = workout.exercises.flatMap((e) => e.sets).filter((s) => !s.done).length;
  if (left > 0) {
    const ok = await showDialog(
      `<h2>Terminer maintenant ?</h2><p class="muted">${left} série(s) pas faite(s). Ce qui est fait est fait 💪</p>`,
      [{ label: "Continuer", value: false }, { label: "Terminer", value: true, className: "primary" }],
    );
    if (!ok) return;
  }

  // The recap is computed here, no server needed
  const finishedAt = new Date();
  const doc = {
    session_id: workout.session_id,
    name: workout.name,
    focus: workout.focus,
    format: workout.format,
    date: workout.date,
    started_at: workout.started_at,
    // never before started_at (phone clock jumped back: DST, manual fix); the server clamps too
    finished_at: localIso(new Date(Math.max(finishedAt, new Date(workout.started_at)))),
    exercises: workout.exercises.map((e) => ({
      slug: e.slug,
      target_reps: e.target_reps,
      sets: e.sets.map(cleanSet),
    })),
  };
  const allSets = doc.exercises.flatMap((e) => e.sets);
  const summary = {
    sets_done: allSets.filter((s) => s.done).length,
    tonnage: tonnage(allSets),
    duration_min: Math.max(0, Math.round((finishedAt - new Date(workout.started_at)) / 60000)),
    // this week's count (already includes finished workouts not yet synced) + this one
    // (+1 only if the workout started in the current week)
    week_count: (snapshot ? snapshot.week_count : 0)
      + (mondayOf(workout.date) === mondayOf(localDate(new Date())) ? 1 : 0),
    week_goal: snapshot ? snapshot.week_goal : 4,
  };

  // Queue first, then forget the in-progress copy (a crash in between only costs a duplicate
  // id, and the server replaces duplicates)
  if (!enqueue("workout", workout.id, doc)) {
    // Phone storage full or blocked: keep the in-progress copy, lose nothing
    await showDialog(
      `<h2>Enregistrement impossible</h2><p class="muted">Le stockage du téléphone est plein ou bloqué. La séance reste ouverte : libère de la place puis réessaie de terminer.</p>`,
      [{ label: "OK", value: true, className: "primary" }],
    );
    return;
  }
  store.remove("workout");
  rebuildSnapshot();  // next session / last weights / week count now include this workout
  workout = null;

  const goalReached = summary.week_count === summary.week_goal;
  await showDialog(`
    <p class="big-emoji">${goalReached ? "🏆" : "✅"}</p>
    <h2>${goalReached ? "Objectif de la semaine atteint !" : "Séance terminée"}</h2>
    <dl class="recap">
      <div><dt>Séries</dt><dd>${Number(summary.sets_done)}</dd></div>
      <div><dt>Tonnage</dt><dd>${Math.round(summary.tonnage)} kg</dd></div>
      <div><dt>Durée</dt><dd>${Number(summary.duration_min)} min</dd></div>
      <div><dt>Semaine</dt><dd>${Number(summary.week_count)}/${Number(summary.week_goal)}</dd></div>
    </dl>`,
    [{ label: "Fermer", value: true, className: "primary" }],
  );
  closeWorkout();
  syncOutbox();  // not awaited: sends it now if the PC is on
}

async function abandonWorkout() {
  const ok = await showDialog(
    `<h2>Abandonner la séance ?</h2><p class="muted">Les séries validées seront effacées.</p>`,
    [{ label: "Annuler", value: false }, { label: "Abandonner", value: true, className: "danger" }],
  );
  if (!ok) return;
  store.remove("workout");  // nothing was ever sent to the server: only the phone copy goes
  workout = null;
  closeWorkout();
}


// --- Wiring ---

function setupWorkout() {
  $("sets").addEventListener("input", onSetInput);
  $("sets").addEventListener("click", onSetTap);
  $("segments").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (button) goTo(Number(button.dataset.index));
  });
  $("prev").addEventListener("click", () => goTo(current - 1));
  $("next").addEventListener("click", () => {
    if (current === workout.exercises.length - 1) finishWorkout();
    else goTo(current + 1);
  });
  $("quit").addEventListener("click", closeWorkout);  // the workout stays saved
  $("abandon").addEventListener("click", () => abandonWorkout().catch(showError));
  $("rest-minus").addEventListener("click", () => { restEndsAt -= 15000; });
  $("rest-plus").addEventListener("click", () => { restEndsAt += 15000; });
  $("rest-skip").addEventListener("click", stopRest);
}
