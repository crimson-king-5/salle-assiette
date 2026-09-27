// "Séance" screen (SPEC §4.2): one exercise at a time,
// 1-tap set validation, automatic rest timer, end-of-workout recap.
// Uses $, api, showError and todayFormat from app.js.

let workout = null;     // the workout in progress, as sent by GET /workouts/current
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

function showDialog(html, buttons) {
  // Resolves with the value of the button tapped (null if dismissed with Escape).
  return new Promise((resolve) => {
    const dialog = $("dialog");
    $("dialog-body").innerHTML = html;
    $("dialog-actions").innerHTML = "";
    for (const b of buttons) {
      const el = document.createElement("button");
      el.type = "button";
      el.textContent = b.label;
      el.className = b.className || "";
      el.addEventListener("click", () => { resolve(b.value); dialog.close(); });
      $("dialog-actions").append(el);
    }
    dialog.onclose = () => resolve(null);
    dialog.showModal();
  });
}


// --- Open / close workout mode ---

async function startWorkout(sessionId) {
  try {
    workout = await api("/workouts", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, format: todayFormat }),
    });
    current = 0;
    openWorkout();
  } catch (error) {
    showError(error);
  }
}

async function resumeWorkout() {
  workout = await api("/workouts/current");
  if (!workout) return;
  // Land on the first exercise that still has sets to do
  const firstUnfinished = workout.exercises.findIndex((e) => e.sets.some((s) => !s.done));
  current = firstUnfinished === -1 ? workout.exercises.length - 1 : firstUnfinished;
  openWorkout();
}

function openWorkout() {
  $("today").hidden = true;
  $("workout").hidden = false;
  $("w-name").textContent = workout.name;
  const startedAt = new Date(workout.started_at).getTime();
  const tick = () => { $("chrono").textContent = mmss((Date.now() - startedAt) / 1000); };
  tick();
  chronoTimer = setInterval(tick, 1000);
  render();
}

function closeWorkout() {
  clearInterval(chronoTimer);
  stopRest();
  $("workout").hidden = true;
  $("today").hidden = false;
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
      return `<button type="button" class="${cls}" data-index="${i}" aria-label="Exercice ${i + 1} : ${e.name}"><span></span></button>`;
    })
    .join("");

  $("ex-name").textContent = ex.name;
  const lastTime = ex.last_weight != null ? ` · Dernière fois : ${ex.last_weight} kg` : "";
  $("ex-info").textContent = `${ex.sets.length} × ${ex.target_reps} reps · repos ${ex.rest_seconds} s${lastTime}`;

  $("sets").innerHTML = ex.sets
    .map((s, i) => {
      const weightHint = (i > 0 && ex.sets[i - 1].weight) || ex.last_weight || "kg";
      return `
      <div class="set-row ${s.done ? "done" : ""}" data-index="${i}">
        <span class="set-num">${i + 1}</span>
        <label><input type="text" inputmode="decimal" data-field="weight" value="${s.weight ?? ""}" placeholder="${weightHint}" aria-label="Série ${i + 1}, kilos"><small>kg</small></label>
        <label><input type="text" inputmode="numeric" data-field="reps" value="${s.reps ?? ""}" placeholder="${topOfRange(ex.target_reps) ?? ""}" aria-label="Série ${i + 1}, répétitions"><small>reps</small></label>
        <button type="button" class="check">${s.done ? "✓" : "Valider"}</button>
      </div>`;
    })
    .join("");

  $("prev").disabled = current === 0;
  $("next").textContent = current === last ? "Terminer" : "Suivant";
}


// --- Sets: typing and 1-tap validation ---

function onSetInput(event) {
  // Keep what's typed, so it survives a re-render
  const input = event.target.closest("input");
  if (!input) return;
  const set = workout.exercises[current].sets[input.closest(".set-row").dataset.index];
  const value = input.value.replace(",", ".");
  set[input.dataset.field] = value === "" ? null : Number(value);
}

async function onSetTap(event) {
  const button = event.target.closest(".check");
  if (!button) return;
  const i = Number(button.closest(".set-row").dataset.index);
  const ex = workout.exercises[current];
  const set = ex.sets[i];

  let body;
  if (set.done) {
    body = { weight: set.weight, reps: set.reps, done: false };  // re-tap = un-validate
  } else {
    // Empty validation: last known weight and top of the rep range (SPEC §4.2)
    const weight = set.weight ?? (i > 0 ? ex.sets[i - 1].weight : null) ?? ex.last_weight;
    const reps = set.reps ?? topOfRange(ex.target_reps);
    body = { weight, reps, done: true };
  }

  try {
    const saved = await api(`/workouts/sets/${set.id}`, { method: "PATCH", body: JSON.stringify(body) });
    Object.assign(set, saved);
    const next = ex.sets[i + 1];
    if (saved.done && next && !next.done && next.weight == null) next.weight = saved.weight;  // carry the weight over
    render();
    if (saved.done) startRest(ex.rest_seconds);
  } catch (error) {
    showError(error);
  }
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

async function finishWorkout() {
  const left = workout.exercises.flatMap((e) => e.sets).filter((s) => !s.done).length;
  if (left > 0) {
    const ok = await showDialog(
      `<h2>Terminer maintenant ?</h2><p class="muted">${left} série(s) pas faite(s). Ce qui est fait est fait 💪</p>`,
      [{ label: "Continuer", value: false }, { label: "Terminer", value: true, className: "primary" }],
    );
    if (!ok) return;
  }
  try {
    const summary = await api(`/workouts/${workout.id}/finish`, { method: "POST" });
    const goalReached = summary.week_count === summary.week_goal;
    await showDialog(`
      <p class="big-emoji">${goalReached ? "🏆" : "✅"}</p>
      <h2>${goalReached ? "Objectif de la semaine atteint !" : "Séance terminée"}</h2>
      <dl class="recap">
        <div><dt>Séries</dt><dd>${summary.sets_done}</dd></div>
        <div><dt>Tonnage</dt><dd>${Math.round(summary.tonnage)} kg</dd></div>
        <div><dt>Durée</dt><dd>${summary.duration_min} min</dd></div>
        <div><dt>Semaine</dt><dd>${summary.week_count}/${summary.week_goal}</dd></div>
      </dl>`,
      [{ label: "Fermer", value: true, className: "primary" }],
    );
    workout = null;
    closeWorkout();
  } catch (error) {
    showError(error);
  }
}

async function abandonWorkout() {
  const ok = await showDialog(
    `<h2>Abandonner la séance ?</h2><p class="muted">Les séries validées seront effacées.</p>`,
    [{ label: "Annuler", value: false }, { label: "Abandonner", value: true, className: "danger" }],
  );
  if (!ok) return;
  await api(`/workouts/${workout.id}`, { method: "DELETE" });
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
