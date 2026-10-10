// Pure helpers, no page access: shared by app.js, workout.js and selftest.html.

// KEEP IN SYNC with compute_readiness() in backend/app/algorithms.py.
const ANSWER_VALUES = [1, 3, 5];

function computeReadiness(energy, sleep, motivation, soreness, minutes) {
  const checks = { energy, sleep, motivation, soreness };
  for (const [name, value] of Object.entries(checks)) {
    if (!ANSWER_VALUES.includes(value)) {
      throw new Error(`${name} must be one of ${ANSWER_VALUES}, got ${value}`);
    }
  }

  const score = energy + sleep + motivation + (6 - soreness);

  let level;
  if (score >= 15) level = "vert";
  else if (score >= 10) level = "orange";
  else level = "rouge";

  let format;
  if (minutes <= 30) format = "express";
  else if (level === "rouge") format = "express";
  else if (level === "vert" && minutes >= 60) format = "complete";
  else format = "minimale";

  return { score, level, format };
}

// KEEP IN SYNC with tonnage() in backend/app/algorithms.py.
// Only validated sets with a weight AND reps count.
function tonnage(sets) {
  let total = 0;
  for (const s of sets) {
    if (s.done && s.weight != null && s.reps != null) {
      total += s.weight * s.reps;
    }
  }
  return total;
}


// --- Dates: always the phone's local time, written without timezone (like the server) ---

const pad2 = (n) => String(n).padStart(2, "0");

function localDate(d) {  // "2026-10-10"
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function localIso(d) {  // "2026-10-10T14:03:22"
  return `${localDate(d)}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

function mondayOf(dateStr) {  // "2026-10-10" (a Saturday) → "2026-10-05"
  const [y, m, d] = dateStr.slice(0, 10).split("-").map(Number);
  const day = new Date(y, m - 1, d);
  day.setDate(day.getDate() - ((day.getDay() + 6) % 7));
  return localDate(day);
}


// --- Misc ---

function newId() {  // client-side UUID for offline-created documents
  if (globalThis.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto && crypto.getRandomValues) crypto.getRandomValues(bytes);
  else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;  // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => pad2(b.toString(16).padStart(2, "0")));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-`
    + `${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

function esc(text) {  // for text put inside innerHTML templates
  return String(text).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}


// --- Snapshot + queued items (see app.js): what the phone shows while the PC doesn't know yet ---
// outbox: not sent yet. sent: accepted by the server but not yet inside the snapshot we hold.
// today: "YYYY-MM-DD" (passed in, so it is
// computed at call time, not frozen).
function applyPending(raw, outbox, sent, today) {
  if (!raw) return null;
  const snap = JSON.parse(JSON.stringify(raw));
  const monday = mondayOf(today);

  // A week count from a snapshot of a previous week no longer applies
  if (mondayOf(raw.generated_at) !== monday) snap.week_count = 0;

  // Everything still in the outbox is by definition NOT on the server (rejected items live in
  // their own list), and "sent" items are not yet inside the snapshot we hold: overlay both.
  const pending = [...sent, ...outbox];

  const workouts = pending.filter((i) => i.type === "workout")
    .sort((a, b) => a.body.finished_at.localeCompare(b.body.finished_at));  // oldest first
  const rotation = snap.sessions.map((s) => s.id);
  for (const { body } of workouts) {
    for (const ex of body.exercises) {
      for (const set of ex.sets) {
        if (set.done && set.weight != null) snap.last_weights[ex.slug] = set.weight;  // latest wins
      }
    }
    const at = rotation.indexOf(body.session_id);
    snap.next_session_id = at === -1 ? rotation[0] : rotation[(at + 1) % rotation.length];
    if (mondayOf(body.date) === monday) snap.week_count++;
  }

  const checkins = pending.filter((i) => i.type === "checkin" && i.body.date === today)
    .sort((a, b) => a.body.created_at.localeCompare(b.body.created_at));
  if (checkins.length > 0) {
    const b = checkins[checkins.length - 1].body;
    snap.checkin_today = computeReadiness(b.energy, b.sleep, b.motivation, b.soreness, b.minutes);
    snap.checkin_date = today;
  }
  return snap;
}
