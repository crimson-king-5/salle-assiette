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

  const protein = proteinState(raw, outbox, sent, today);
  snap.protein_today = protein.entries;
  snap.protein_goal_g = protein.goal;
  snap.weights = weightsState(raw, outbox, sent);
  return snap;
}


// --- Outbox revisions ---
// Every queued item gets a `rev`. While an item is being sent, the user may queue a newer copy
// of it (same type + id, other rev): the answer for the old copy must not remove the new one.
function newRev() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// The outbox without the entry that was actually sent (same type, id AND rev).
function withoutSent(outbox, sentItem) {
  return outbox.filter((i) => !(i.type === sentItem.type && i.id === sentItem.id && i.rev === sentItem.rev));
}


// --- Protein journal (SPEC §4.7) ---
// Outbox item types: "protein" = PUT a line (add or correct), "protein-delete" = DELETE it.

const DEFAULT_PROTEIN_GOAL = 160;
const MAX_LINE_GRAMS = 300;  // KEEP IN SYNC with ProteinSyncIn in backend/app/schemas.py

// Today's journal as the phone must show it: the cached snapshot's lines, then every queued or
// just-sent change on top. A delete wins over any add/edit of the same id, whatever the order
// (ids are never reused). `raw` may be null (the PC was never reached).
function proteinState(raw, outbox, sent, today) {
  const lines = new Map();  // id -> { id, date, label, grams, created_at }
  const keep = (line) => { if (line.date === today) lines.set(line.id, { ...line }); };

  for (const line of (raw && raw.protein_today) || []) keep(line);

  const pending = [...sent, ...outbox];  // sent first: an outbox edit is newer than a sent one
  for (const item of pending) {
    if (item.type === "protein") keep({ id: item.id, ...item.body });
  }
  for (const item of pending) {
    if (item.type === "protein-delete") lines.delete(item.id);
  }

  const entries = [...lines.values()]
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  const goal = (raw && Number(raw.protein_goal_g)) || DEFAULT_PROTEIN_GOAL;
  return { entries, goal };
}

function proteinTotal(entries) {
  return entries.reduce((sum, e) => sum + (Number(e.grams) || 0), 0);
}

// --- Weigh-ins (SPEC §4.8) ---
// Outbox item types: "weight" = PUT a weigh-in { date, kg, created_at } (add or correct),
// "weight-delete" = DELETE it. Same local-first pattern as the protein journal.

const MIN_KG = 30;   // KEEP IN SYNC with WeightSyncIn in backend/app/schemas.py
const MAX_KG = 250;

// The weigh-ins as the phone must show them: the snapshot's list, then every queued or just-sent
// change on top, ONE per day (like the server: the most recently made one wins; a snapshot line
// from an old cache may lack created_at: then a queued change beats it). Oldest first. `raw` may be null.
function weightsState(raw, outbox, sent) {
  const rows = new Map();  // id -> { id, date, kg, created_at }
  for (const w of (raw && raw.weights) || []) rows.set(w.id, { id: w.id, date: w.date, kg: w.kg, created_at: w.created_at ?? "" });

  const pending = [...sent, ...outbox];  // sent first: an outbox edit is newer than a sent one
  for (const item of pending) {
    if (item.type === "weight") rows.set(item.id, { id: item.id, ...item.body });
  }
  for (const item of pending) {
    if (item.type === "weight-delete") rows.delete(item.id);  // a delete wins, whatever the order
  }

  const perDay = new Map();
  const byMadeAt = [...rows.values()].sort((a, b) =>
    String(a.created_at).localeCompare(String(b.created_at)) || a.id.localeCompare(b.id));
  for (const w of byMadeAt) perDay.set(w.date, w);  // ascending: the last one of a day wins
  return [...perDay.values()].sort((a, b) => a.date.localeCompare(b.date));
}

// KEEP IN SYNC with PROTEIN_PER_KG / auto_protein_goal() in backend/app/algorithms.py.
const PROTEIN_PER_KG = { recompo: 2.0, perte: 2.0, muscle: 1.8, forme: 1.6 };

function autoProteinGoal(weightKg, objective) {
  const grams = Math.floor(weightKg * (PROTEIN_PER_KG[objective] ?? 2.0) + 0.5);
  return Math.max(60, Math.min(300, grams));
}

// "2026-10-10" -> local Date at midnight; and the number of days since 1970 (DST-proof)
function parseDay(dateStr) {
  const [y, m, d] = String(dateStr).slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}
function dayNumber(dateStr) {
  return Math.round(parseDay(dateStr).getTime() / 86400000);
}


// Grams of protein in `grams` g of a food that has `per100` g of protein per 100 g. Whole grams.
function proteinFor(per100, grams) {
  return Math.round((Number(per100) * Number(grams)) / 100);
}

// A journal label: trimmed, single spaces, at most `max` characters (80 = the server's limit).
// Cut by code points (an emoji is never split in two) and lone surrogates are dropped: they
// cannot be encoded as UTF-8 and the server would refuse the line for good.
function cleanLabel(text, max = 80) {
  const clean = String(text ?? "")
    .replace(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g, "")
    .replace(/\s+/g, " ").trim();
  return Array.from(clean).slice(0, max).join("").trim();
}

// Portions used to turn "grams left to eat" into something concrete. Same values as the shortcuts.
const SUGGESTION_PORTIONS = [
  { label: "1 blanc de poulet", grams: 35 },
  { label: "1 steak", grams: 26 },
  { label: "1 boîte de thon", grams: 25 },
  { label: "1 shake de whey", grams: 24 },
  { label: "3 œufs", grams: 18 },
  { label: "1 portion de lentilles", grams: 18 },
  { label: "1 fromage blanc", grams: 16 },
  { label: "1 skyr", grams: 15 },
];

// remaining grams → { items: [portion…], left } : up to 3 different portions, each time the
// biggest one that still fits. 50 g → poulet (35) + skyr (15). `left` = grams not covered.
// Under the smallest portion: one skyr if at least 8 g are missing, otherwise nothing.
function suggestPortions(remaining, portions = SUGGESTION_PORTIONS) {
  const sorted = [...portions].sort((a, b) => b.grams - a.grams);  // stable: ties keep list order
  const smallest = sorted[sorted.length - 1];
  const items = [];
  let left = Math.round(remaining);
  while (left > 0 && items.length < 3) {
    const fit = sorted.find((p) => p.grams <= left && !items.includes(p));
    if (fit) { items.push(fit); left -= fit.grams; continue; }
    if (items.length === 0 && left >= 8) { items.push(smallest); left = Math.max(0, left - smallest.grams); }
    break;
  }
  return { items, left: Math.max(0, left) };
}


// --- Food search (accent-insensitive) ---

// "Œufs Durs" → "oeufs durs", "Crème fraîche" → "creme fraiche"
function normalizeText(text) {
  return String(text ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/œ/gi, "oe").replace(/æ/gi, "ae").toLowerCase().trim();
}

// foods: [{ name, alias?, … }]. Every word of the query must START a word of the name or the alias;
// names that start with the query come first. Empty query → [].
function searchFoods(query, foods) {
  const words = normalizeText(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  const first = words[0];
  return foods
    .map((food) => ({
      food,
      name: normalizeText(food.name),
      tokens: normalizeText(`${food.name} ${food.alias ?? ""}`).split(/[^a-z0-9]+/),
    }))
    .filter((x) => words.every((w) => x.tokens.some((t) => t.startsWith(w))))  // word starts: "oeuf" is not "boeuf"
    .sort((a, b) => Number(b.name.startsWith(first)) - Number(a.name.startsWith(first)))  // stable
    .map((x) => x.food);
}


// --- Open Food Facts (barcode) ---

// Only digits, 8 to 14 of them (EAN-8 … GTIN-14): checked before the code goes into a URL.
function validBarcode(code) {
  return /^\d{8,14}$/.test(String(code ?? "").replace(/\s+/g, ""));
}

// "30 g" → 30, "1 pot (125 g)" → 125, "2 x 60g" → 60, "250 ml" → null. Whole grams, 1 to 1000.
function parseServingGrams(text) {
  const match = /(\d+(?:[.,]\d+)?)\s*g(?![a-z])/i.exec(String(text ?? ""));
  if (!match) return null;
  const grams = Math.round(Number(match[1].replace(",", ".")));
  return grams >= 1 && grams <= 1000 ? grams : null;
}

// The JSON answer of Open Food Facts → what the screen needs.
//   { kind: "notfound" } | { kind: "noprotein", name, brand } |
//   { kind: "ok", name, brand, per100, servingGrams }
function interpretOffProduct(json) {
  const product = json && json.status !== 0 && json.product;
  if (!product || typeof product !== "object") return { kind: "notfound" };
  const name = cleanLabel(product.product_name) || "Produit sans nom";
  const brand = cleanLabel(String(product.brands ?? "").split(",")[0]);
  const raw = product.proteins_100g;
  const per100 = raw === "" || raw == null ? NaN : Number(raw);
  if (!Number.isFinite(per100) || per100 < 0 || per100 > 100) return { kind: "noprotein", name, brand };
  return { kind: "ok", name, brand, per100, servingGrams: parseServingGrams(product.serving_size) };
}
