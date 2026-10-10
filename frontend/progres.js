// Salle & Assiette — "Progrès" screen (SPEC §4.8). Read-only.
// Everything is drawn from the snapshot (GET /snapshot, kept on the phone by app.js): weigh-ins,
// the server's progress numbers (records, last workouts, streak, cardio) and the progression tips.
// So it works with the PC off and then says "données du …". The weigh-in curve also includes
// the weigh-ins still waiting in the outbox (applyPending in logic.js).
// Uses $, snapshot, offline, getOutbox (app.js), addWeightFlow (reglages.js) and the helpers of logic.js.

const NUMBER = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });
const kgText = (kg) => `${NUMBER.format(Math.round(kg * 10) / 10)} kg`;

function dayText(dateStr, withYear = false) {  // "2026-10-05" -> "5 oct."
  return parseDay(dateStr).toLocaleDateString("fr-FR", { day: "numeric", month: "short", ...(withYear ? { year: "numeric" } : {}) });
}

const FORMAT_NAMES = { complete: "complète", minimale: "minimale", express: "express" };

// Gauges are sized through the DOM API (a style="" attribute would break the CSP)
function applyGauges(root) {
  root.querySelectorAll(".gauge > div[data-pct]").forEach((el) => {
    el.style.width = `${Math.max(0, Math.min(100, Number(el.dataset.pct) || 0))}%`;
  });
}


// --- Weight curve: inline SVG, no library ---

function weightChart(points, target) {
  // points: [{ date, kg }] oldest first, at least 2. target: kg or null (dashed goal line)
  const W = 320, H = 170, left = 8, right = 8, top = 16, bottom = 26;
  const t0 = dayNumber(points[0].date);
  const span = Math.max(1, dayNumber(points[points.length - 1].date) - t0);
  const values = points.map((p) => p.kg);
  if (target != null) values.push(target);
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  const pad = Math.max(0.5, (hi - lo) * 0.12);
  lo -= pad;
  hi += pad;
  const x = (date) => left + ((dayNumber(date) - t0) / span) * (W - left - right);
  const y = (kg) => top + ((hi - kg) / (hi - lo)) * (H - top - bottom);
  const f = (n) => n.toFixed(1);

  const line = points.map((p) => `${f(x(p.date))},${f(y(p.kg))}`).join(" ");
  const last = points[points.length - 1];
  let goal = "";
  if (target != null) {
    const gy = y(target);
    const labelY = gy - 5 < 12 ? gy + 15 : gy - 5;
    goal = `<line class="chart-target" x1="${left}" x2="${W - right}" y1="${f(gy)}" y2="${f(gy)}"/>
      <text class="chart-text chart-go" x="${W - right}" y="${f(labelY)}" text-anchor="end">objectif ${esc(NUMBER.format(target))} kg</text>`;
  }
  const label = `Courbe de poids de ${NUMBER.format(points[0].kg)} à ${NUMBER.format(last.kg)} kilos, `
    + `du ${dayText(points[0].date)} au ${dayText(last.date)}`
    + (target != null ? `, objectif ${NUMBER.format(target)} kilos` : "");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
      <line class="chart-axis" x1="${left}" x2="${W - right}" y1="${H - bottom + 4}" y2="${H - bottom + 4}"/>
      ${goal}
      <polyline class="chart-line" points="${line}"/>
      <circle class="chart-dot" cx="${f(x(last.date))}" cy="${f(y(last.kg))}" r="5"/>
      <text class="chart-text" x="${left}" y="${H - 6}">${esc(dayText(points[0].date))}</text>
      <text class="chart-text" x="${W - right}" y="${H - 6}" text-anchor="end">${esc(dayText(last.date))}</text>
    </svg>`;
}

function weightCard(snap) {
  const weights = snap.weights || [];
  const target = Number(snap.target_weight_kg) || null;
  const add = `<button type="button" class="wide" data-act="add-weight">Ajouter une pesée</button>`;

  if (weights.length === 0) {
    return `<section class="card" aria-labelledby="p-weight-title">
      <h2 id="p-weight-title">Poids</h2>
      <p class="muted">Pas encore de pesée. Une par semaine suffit : la courbe se dessine toute seule.</p>
      ${add}</section>`;
  }

  const first = weights[0];
  const last = weights[weights.length - 1];
  let gap = "";
  if (target != null) {
    const diff = last.kg - target;
    if (Math.abs(diff) < 0.3) gap = "Objectif atteint 🎉";
    else gap = `encore ${NUMBER.format(Math.round(Math.abs(diff) * 10) / 10)} kg à ${diff > 0 ? "perdre" : "prendre"}`;
  }
  let body;
  if (weights.length === 1) {
    body = `<p class="muted">Une seule pesée pour l'instant (${esc(dayText(last.date))}). La courbe apparaît dès la deuxième.</p>`;
  } else {
    const delta = Math.round((last.kg - first.kg) * 10) / 10;
    const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
    body = `${weightChart(weights, target)}
      <p class="muted">${sign}${esc(NUMBER.format(Math.abs(delta)))} kg depuis le ${esc(dayText(first.date))}</p>`;
  }
  return `<section class="card" aria-labelledby="p-weight-title">
      <h2 id="p-weight-title">Poids</h2>
      <p class="big-figure"><strong>${esc(kgText(last.kg))}</strong>${gap ? `<span> · ${esc(gap)}</span>` : ""}</p>
      ${body}
      ${add}</section>`;
}


// --- Tonnage history: bars of the last finished workouts ---

function tonnageChart(workouts) {
  // workouts: oldest first, 1 to 6 of them
  const W = 320, H = 150, top = 20, bottom = 24;
  const max = Math.max(...workouts.map((w) => w.tonnage), 1);
  const slot = W / workouts.length;
  const barW = Math.min(36, slot - 12);
  const bars = workouts.map((w, i) => {
    const h = Math.max(2, (w.tonnage / max) * (H - top - bottom));
    const cx = slot * i + slot / 2;
    return `<rect class="chart-bar" x="${(cx - barW / 2).toFixed(1)}" y="${(H - bottom - h).toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}"/>
      <text class="chart-text chart-center" x="${cx.toFixed(1)}" y="${(H - bottom - h - 5).toFixed(1)}" text-anchor="middle">${esc(NUMBER.format(Math.round(w.tonnage / 100) / 10))}</text>
      <text class="chart-text chart-center" x="${cx.toFixed(1)}" y="${H - 6}" text-anchor="middle">${esc(dayText(w.date))}</text>`;
  }).join("");
  const label = "Tonnage des dernières séances, en tonnes : "
    + workouts.map((w) => `${dayText(w.date)} ${Math.round(w.tonnage / 100) / 10}`).join(", ");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
      <line class="chart-axis" x1="0" x2="${W}" y1="${H - bottom + 4}" y2="${H - bottom + 4}"/>${bars}</svg>`;
}


// --- The screen ---

function statTile(value, label, extra = "") {
  return `<div class="stat"><strong>${value}${extra}</strong><span>${esc(label)}</span></div>`;
}

function renderProgress() {
  const snap = snapshot;
  const body = $("p-body");
  $("p-offline").hidden = !(offline && snap);
  if (offline && snap) {
    const when = new Date(snap.generated_at).toLocaleString("fr-FR", {
      weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit",
    });
    $("p-offline").textContent = `📴 Hors ligne · données du ${when}`;
  }

  if (!snap) {
    body.innerHTML = `<section class="card"><p class="muted">Aucune donnée sur ce téléphone pour l'instant. Ouvre l'app une première fois avec le PC allumé.</p></section>`;
    return;
  }
  const progress = snap.progress;
  if (!progress) {  // a snapshot saved by an older version of the app
    body.innerHTML = `${weightCard(snap)}<section class="card"><p class="muted">Tes statistiques arrivent dès que le PC répond.</p></section>`;
    return;
  }

  const waiting = getOutbox().filter((i) => i.type === "workout").length;
  const cardioPct = progress.cardio_week_target_min ? (progress.cardio_minutes_week / progress.cardio_week_target_min) * 100 : 0;
  const recent = progress.recent_workouts || [];
  const records = progress.records || [];
  const tips = snap.tips || {};
  const goalLeft = Math.max(0, snap.week_goal - snap.week_count);

  const sessionsCard = progress.total_workouts === 0
    ? `<section class="card"><h2>Séances</h2>
        <p class="muted">Aucune séance terminée pour l'instant. Ta première apparaîtra ici avec ses records : rendez-vous dans Aujourd'hui.</p></section>`
    : `<section class="card" aria-label="Régularité">
        <div class="stats">
          ${statTile(Number(progress.total_workouts), progress.total_workouts > 1 ? "séances" : "séance")}
          ${statTile(Number(progress.weeks_held), progress.weeks_held > 1 ? "semaines tenues" : "semaine tenue")}
          ${statTile(Number(snap.week_count), "cette semaine", `<small> / ${Number(snap.week_goal)}</small>`)}
        </div>
        <p class="muted stat-note">${goalLeft === 0 ? "Objectif de la semaine atteint 🏆"
          : `Encore ${goalLeft} séance${goalLeft > 1 ? "s" : ""} pour tenir la semaine.`}</p>
        ${waiting > 0 ? `<p class="muted stat-note">${waiting} séance${waiting > 1 ? "s" : ""} en attente de synchro, pas encore dans les totaux.</p>` : ""}
      </section>`;

  const cardioCard = `<section class="card" aria-labelledby="p-cardio-title">
      <h2 id="p-cardio-title">Cardio de la semaine</h2>
      <p class="protein-total"><strong class="cardio-figure">${Number(progress.cardio_minutes_week)}</strong><span> / ${Number(progress.cardio_week_target_min)} min</span></p>
      <div class="gauge cardio" data-done="${cardioPct >= 100 ? 1 : ""}" role="progressbar" aria-label="Minutes de cardio de la semaine"
        aria-valuemin="0" aria-valuemax="${Number(progress.cardio_week_target_min)}" aria-valuenow="${Math.min(Number(progress.cardio_minutes_week), Number(progress.cardio_week_target_min))}">
        <div data-pct="${cardioPct}"></div>
      </div>
      <p class="muted stat-note">${cardioPct >= 100 ? "Repère de 150 min atteint, bravo." : "Repère : 150 min par semaine, en plusieurs fois."}</p>
    </section>`;

  const tonnageCard = `<section class="card" aria-labelledby="p-ton-title">
      <h2 id="p-ton-title">Tonnage</h2>
      ${recent.length === 0
        ? `<p class="muted">Le tonnage (charge × répétitions) de tes dernières séances s'affichera ici.</p>`
        : `<p class="muted">En tonnes, tes ${recent.length > 1 ? `${recent.length} dernières séances` : "dernière séance"} (cardio exclu).</p>
           ${tonnageChart([...recent].reverse())}
           <ul class="rows">${recent.map((w) => `<li>
              <span class="jr-main"><span class="jr-label">${esc(w.name)}</span>
                <small>${esc(dayText(w.date))} · ${esc(FORMAT_NAMES[w.format] || w.format)} · ${Number(w.duration_min)} min · ${Number(w.sets_done)} séries</small></span>
              <span class="jr-val">${esc(NUMBER.format(Math.round(w.tonnage)))} kg</span></li>`).join("")}</ul>`}
    </section>`;

  const recordsCard = `<section class="card" aria-labelledby="p-rec-title">
      <h2 id="p-rec-title">Records</h2>
      ${records.length === 0
        ? `<p class="muted">Pas encore de record : ils apparaissent dès ta première séance avec des charges.</p>`
        : `<ul class="rows">${records.map((r) => `<li class="stack">
            <span class="row-line"><span class="jr-label">${esc(r.name)}</span>
              <span class="jr-val">${esc(NUMBER.format(r.weight))} kg × ${Number(r.reps)}</span></span>
            ${tips[r.slug] ? `<span class="tip">💡 ${esc(tips[r.slug])}</span>` : ""}
            <small class="row-date">${esc(dayText(r.date, true))}</small></li>`).join("")}</ul>`}
    </section>`;

  const proteinCard = `<section class="card" aria-labelledby="p-prot-title">
      <h2 id="p-prot-title">Protéines</h2>
      <p class="muted">Aujourd'hui : <strong class="inline-protein">${proteinTotal(snap.protein_today || [])} g</strong> sur ${Number(snap.protein_goal_g)} g.
        L'historique jour par jour n'est pas gardé pour l'instant.</p>
    </section>`;

  body.innerHTML = weightCard(snap) + sessionsCard + cardioCard + tonnageCard + recordsCard + proteinCard;
  applyGauges(body);
}

function setupProgress() {
  $("p-body").addEventListener("click", (event) => {
    if (event.target.closest("[data-act='add-weight']")) addWeightFlow().catch(reglagesError);
  });
  renderProgress();
}
