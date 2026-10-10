// Salle & Assiette — tab bar + "Assiette" screen (SPEC §4.7).
// Local-first like the rest of the app: every journal change is saved on the phone at once and
// queued in the outbox (see app.js): { type: "protein", id, body } = add or correct a line (PUT),
// { type: "protein-delete", id } = remove it (DELETE). The counter is always computed from the
// last snapshot + the queue (proteinState in logic.js), so it is right with the PC off.
// Uses $, store, rawSnapshot, enqueue, getOutbox, getSent, renderPending, syncOutbox (app.js),
// showDialog (workout.js), SHORTCUTS, FOODS… (assiette-data.js) and the helpers of logic.js.


// --- Tabs (Aujourd'hui / Assiette …) ---
// Each tab is a <main id=name class="tab">. Workout mode hides all of them and the tab bar.

const TABS = ["today", "assiette", "progres", "coach", "reglages"];
let currentTab = "today";

function showTab(name) {
  if (!TABS.includes(name)) name = "today";
  currentTab = name;
  for (const tab of TABS) $(tab).hidden = tab !== name;
  document.querySelectorAll("#tabbar button").forEach((button) => {
    const selected = button.dataset.tab === name;
    button.classList.toggle("selected", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  store.set("tab", name);
  window.scrollTo(0, 0);
  if (name === "assiette") { renderAssiette(); renderMealCoach(); }
  if (name === "coach") openCoachTab();
  if (name === "progres") renderProgress();
  if (name === "reglages") renderReglages();  // keeps an open sub-view (and its draft); redraws the home
}

function enterWorkoutMode() {
  for (const tab of TABS) $(tab).hidden = true;
  $("tabbar").hidden = true;
}

function leaveWorkoutMode() {
  $("tabbar").hidden = false;
  showTab(currentTab);
}


// --- Small helpers ---

let toastTimer = null;

function showToast(text, actionLabel, onAction, ms = 5000) {
  $("toast-text").textContent = text;
  $("toast-action").hidden = !actionLabel;
  $("toast-action").textContent = actionLabel || "";
  $("toast-action").onclick = () => { hideToast(); if (onAction) onAction(); };
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, ms);
}

function hideToast() {
  clearTimeout(toastTimer);
  $("toast").hidden = true;
}

// showDialog + "Enter" in a field presses the main (blue) button
async function showForm(html, buttons) {
  const shown = showDialog(html, buttons);
  $("dialog-body").onkeydown = (event) => {
    if (event.key !== "Enter" || event.target.tagName !== "INPUT") return;
    event.preventDefault();
    const main = $("dialog-actions").querySelector(".primary");
    if (main) main.click();
  };
  try { return await shown; } finally { $("dialog-body").onkeydown = null; }
}

// showError (app.js) writes into the Today screen, which is hidden here: use the toast instead
function assietteError(error) {
  showToast(error instanceof OfflineError ? "📴 PC injoignable." : `Oups : ${error.message}`, null, null, 6000);
}

const formError = (message) => (message ? `<p class="form-error" role="alert">${esc(message)}</p>` : "");

function storageFullDialog() {
  return showDialog(
    `<h2>Enregistrement impossible</h2><p class="muted">Le stockage du téléphone est plein ou bloqué. Libère de la place puis réessaie.</p>`,
    [{ label: "OK", value: true, className: "primary" }],
  );
}


// --- The journal: add / correct / delete (all offline-first) ---

function currentProtein() {
  return proteinState(rawSnapshot, getOutbox(), getSent(), localDate(new Date()));
}

// Returns the new line's id, or null when the phone could not save it.
function addProteinLine(label, grams) {
  const now = new Date();
  const id = newId();
  const saved = enqueue("protein", id, {
    date: localDate(now), label: cleanLabel(label), grams: Math.round(grams), created_at: localIso(now),
  });
  if (!saved) { storageFullDialog(); return null; }
  afterJournalChange();
  return id;
}

function correctProteinLine(entry, label, grams) {
  // Same id, same date and creation time (keeps its place in the list); the server replaces the line
  const saved = enqueue("protein", entry.id, {
    date: entry.date, label: cleanLabel(label), grams: Math.round(grams), created_at: entry.created_at,
  });
  if (!saved) { storageFullDialog(); return false; }
  afterJournalChange();
  return true;
}

function deleteProteinLine(id) {
  // A delete replaces any waiting add/edit of that line (one write = nothing half-saved)
  const box = getOutbox().filter((i) => !(i.id === id && (i.type === "protein" || i.type === "protein-delete")));
  box.push({ type: "protein-delete", id, body: null, rev: newRev() });
  if (!store.set("outbox", box)) { storageFullDialog(); return false; }
  afterJournalChange();
  return true;
}

function afterJournalChange() {
  renderPending();
  renderAssiette();
  renderToday();  // the protein bar on the Today screen
  syncOutbox();  // not awaited: sends it now if the PC is on
}

function quickAdd(shortcut) {
  const id = addProteinLine(shortcut.label, shortcut.grams);
  if (id) showToast(`${shortcut.label} : +${shortcut.grams} g`, "Annuler", () => deleteProteinLine(id));
}


// --- Drawing the screen ---

function renderAssiette() {
  const { entries, goal } = currentProtein();
  const total = proteinTotal(entries);
  const remaining = goal - total;

  $("a-date").textContent = new Date().toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
  $("a-total").textContent = String(total);
  $("a-goal").textContent = String(goal);
  $("a-fill").style.width = `${Math.min(100, (total / goal) * 100)}%`;
  $("a-gauge").dataset.done = total >= goal ? "1" : "";
  $("a-gauge").setAttribute("aria-valuemax", String(goal));
  $("a-gauge").setAttribute("aria-valuenow", String(Math.min(total, goal)));

  if (remaining <= 0) {
    $("a-remaining").textContent = "Objectif atteint 🎉 Bravo. Le reste, c'est du bonus.";
    $("a-suggest").textContent = "";
  } else {
    $("a-remaining").textContent = total === 0 ? `Il reste ${remaining} g à manger aujourd'hui.` : `Reste ≈ ${remaining} g à manger.`;
    const { items, left } = suggestPortions(remaining);
    if (items.length === 0) {
      $("a-suggest").textContent = "Presque ! Un petit en-cas et c'est bon.";
    } else {
      const more = left >= 10 ? `, puis ≈ ${left} g à répartir sur le reste de la journée` : "";
      $("a-suggest").textContent = `≈ ${items.map((p) => p.label).join(" + ")}${more}.`;
    }
  }

  $("a-journal-empty").hidden = entries.length > 0;
  $("a-journal").innerHTML = [...entries].reverse().map((e) => `
    <li><button type="button" class="journal-row" data-id="${esc(e.id)}" aria-label="Modifier : ${esc(e.label)}, ${Number(e.grams)} g">
      <span class="jr-main"><span class="jr-label">${esc(e.label)}</span><small>${esc(String(e.created_at).slice(11, 16))}</small></span>
      <span class="jr-grams">${Number(e.grams)} g</span>
    </button></li>`).join("");
}

function renderShortcuts() {
  $("a-shortcuts").innerHTML = SHORTCUTS.map((s, i) => `
    <button type="button" class="shortcut" data-i="${i}">
      <span class="sc-label"><span aria-hidden="true">${esc(s.emoji)}</span> ${esc(s.label)}</span>
      <span class="sc-grams">+${Number(s.grams)} g</span>
    </button>`).join("");
}

function renderEatOut() {
  $("a-eatout-body").innerHTML = `
    <p class="muted">Aucun aliment interdit. Vise ≈ 30 g de protéines dans l'assiette, le reste suit : c'est du 80/20.</p>
    <figure class="plate-figure">
      ${plateSvg()}
      <figcaption><ul class="legend">${PLATE_LEGEND.map((l) => `<li><span class="dot ${l.cls}"></span>${esc(l.text)}</li>`).join("")}</ul></figcaption>
    </figure>
    ${EAT_OUT.map((v) => `
      <section class="venue">
        <h3><span aria-hidden="true">${esc(v.emoji)}</span> ${esc(v.title)}</h3>
        <ul>${v.picks.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
        <p class="tip">💡 ${esc(v.tip)}</p>
      </section>`).join("")}`;
}

function renderMealPrep() {
  const checked = new Set(store.get("shop") || []);
  const list = shoppingList();
  $("a-mealprep-body").innerHTML = `
    <p class="muted">6 plats à cuisiner le dimanche pour la semaine. Les protéines sont par portion.</p>
    ${MEAL_PREP.map((d) => `
      <article class="dish">
        <div class="dish-pic">${d.svg}</div>
        <div>
          <h3>${esc(d.name)}</h3>
          <p class="dish-protein">≈ ${Number(d.protein)} g de protéines</p>
          <p class="dish-recipe">${esc(d.recipe)}</p>
        </div>
      </article>`).join("")}
    <h3 class="shop-title">Liste de courses</h3>
    <p class="muted">Pour les 6 plats. Tes coches sont gardées sur ce téléphone.</p>
    <ul class="shop" id="a-shop">${list.map((item) => `
      <li><label>
        <input type="checkbox" data-key="${esc(item.key)}" ${checked.has(item.key) ? "checked" : ""}>
        <span>${esc(item.name)} <small>${esc(item.dishes.join(", "))}</small></span>
      </label></li>`).join("")}</ul>
    <button type="button" class="ghost" id="a-shop-reset">Tout décocher</button>`;
}

function saveShopChecks() {
  const keys = [...document.querySelectorAll("#a-shop input:checked")].map((box) => box.dataset.key);
  store.set("shop", keys);
}


// --- Dialogs: free entry, correcting a line, quantity ---

async function freeEntry(prefill = {}) {
  let draft = { label: prefill.label ?? "", grams: prefill.grams ?? "" };
  let error = "";
  for (;;) {
    const action = await showForm(`
      <h2>Saisie libre</h2>
      <div class="form">
        <label>Quoi ?<input id="f-label" maxlength="80" autocomplete="off" value="${esc(draft.label)}" placeholder="ex. Pavé de saumon"></label>
        <label>Protéines (g)<input id="f-grams" type="number" inputmode="numeric" min="1" max="${MAX_LINE_GRAMS}" value="${esc(draft.grams)}" placeholder="ex. 30"></label>
        ${formError(error)}
      </div>`,
      [{ label: "Annuler", value: null }, { label: "Ajouter", value: "add", className: "primary" }],
    );
    if (action !== "add") return false;
    draft = { label: $("f-label").value, grams: $("f-grams").value };
    const label = cleanLabel(draft.label);
    const grams = Math.round(Number(draft.grams));
    if (!label) error = "Dis-moi ce que tu as mangé 🙂";
    else if (!(grams >= 1 && grams <= MAX_LINE_GRAMS)) error = `Entre un nombre de grammes de protéines entre 1 et ${MAX_LINE_GRAMS}.`;
    else return addProteinLine(label, grams) !== null;
  }
}

async function editLine(id) {
  const entry = currentProtein().entries.find((e) => e.id === id);
  if (!entry) return;
  let draft = { label: entry.label, grams: String(entry.grams) };
  let error = "";
  for (;;) {
    const action = await showForm(`
      <h2>Modifier la ligne</h2>
      <div class="form">
        <label>Quoi ?<input id="e-label" maxlength="80" autocomplete="off" value="${esc(draft.label)}"></label>
        <label>Protéines (g)<input id="e-grams" type="number" inputmode="numeric" min="0" max="${MAX_LINE_GRAMS}" value="${esc(draft.grams)}"></label>
        <p class="muted hint">0 g supprime la ligne.</p>
        ${formError(error)}
      </div>`,
      [
        { label: "Annuler", value: null },
        { label: "Supprimer", value: "delete", className: "danger" },
        { label: "Enregistrer", value: "save", className: "primary" },
      ],
    );
    if (action === "delete") { deleteProteinLine(id); return; }
    if (action !== "save") return;
    draft = { label: $("e-label").value, grams: $("e-grams").value };
    const label = cleanLabel(draft.label);
    const grams = Math.round(Number(draft.grams));
    if (!label) error = "Un petit nom, même court 🙂";
    else if (!(grams >= 0 && grams <= MAX_LINE_GRAMS)) error = `Entre un nombre entre 0 et ${MAX_LINE_GRAMS}.`;
    else {
      if (grams === 0) deleteProteinLine(id);
      else correctProteinLine(entry, label, grams);
      return;
    }
  }
}

// Asks how much of a food was eaten and adds the protein. Returns true when a line was added.
//   food: { name, per100, defaultGrams (or null), note }
async function askQuantity(food) {
  const preview = () => {
    const eaten = Number($("q-grams").value);
    $("q-preview").textContent = eaten > 0
      ? `≈ ${proteinFor(food.per100, eaten)} g de protéines` : "Entre la quantité mangée.";
  };
  let draft = food.defaultGrams ? String(food.defaultGrams) : "";
  let error = "";
  for (;;) {
    const shown = showForm(`
      <h2>${esc(food.name)}</h2>
      <p class="muted">${esc(food.note)}</p>
      <div class="form">
        <label>Quantité mangée (g)<input id="q-grams" type="number" inputmode="decimal" min="1" max="3000" value="${esc(draft)}" placeholder="ex. 150"></label>
        <p class="q-preview" id="q-preview" aria-live="polite"></p>
        ${formError(error)}
      </div>`,
      [{ label: "Annuler", value: null }, { label: "Ajouter", value: "add", className: "primary" }],
    );
    $("dialog-body").oninput = preview;
    preview();
    const action = await shown;
    $("dialog-body").oninput = null;
    if (action !== "add") return false;
    draft = $("q-grams").value;
    const eaten = Math.round(Number(draft));
    const protein = proteinFor(food.per100, eaten);
    if (!(eaten >= 1 && eaten <= 3000)) error = "Entre une quantité en grammes.";
    else if (protein < 1) error = "Ça fait moins de 1 g de protéines : rien à ajouter.";
    else if (protein > MAX_LINE_GRAMS) error = `Ça fait ${protein} g d'un coup : coupe en deux lignes (max ${MAX_LINE_GRAMS} g).`;
    else return addProteinLine(`${cleanLabel(food.name, 60)} · ${eaten} g`, protein) !== null;
  }
}


// --- Built-in food list ---

async function searchFlow() {
  let picked = null;
  const body = $("dialog-body");
  const shown = showDialog(`
    <h2>Chercher un aliment</h2>
    <p class="muted">Valeurs indicatives (moyennes approximatives, aliments prêts à manger).</p>
    <div class="form"><input id="s-query" type="search" autocomplete="off" placeholder="poulet, skyr, kebab…" aria-label="Chercher un aliment"></div>
    <ul class="results" id="s-results"></ul>`,
    [{ label: "Fermer", value: null }],
  );
  const draw = () => {
    const query = $("s-query").value;
    const foods = query.trim() ? searchFoods(query, FOODS) : FOODS;
    $("s-results").innerHTML = foods.length === 0
      ? `<li class="muted">Rien trouvé. Tu peux utiliser la saisie libre.</li>`
      : foods.map((f) => `<li><button type="button" data-i="${FOODS.indexOf(f)}">
          <span>${esc(f.name)}</span>
          <small>${esc(String(f.per100).replace(".", ","))} g de protéines / 100 g · ${esc(f.portion)} ≈ ${Number(f.portionG)} g</small>
        </button></li>`).join("");
  };
  body.oninput = draw;
  body.onclick = (event) => {
    const button = event.target.closest("button[data-i]");
    if (!button) return;
    picked = FOODS[Number(button.dataset.i)];
    $("dialog").close();
  };
  draw();
  await shown;
  body.oninput = null;
  body.onclick = null;
  if (!picked) return;
  await askQuantity({
    name: picked.name,
    per100: picked.per100,
    defaultGrams: picked.portionG,
    note: `${String(picked.per100).replace(".", ",")} g de protéines / 100 g (valeur indicative) · ${picked.portion}`,
  });
}


// --- Barcode: Open Food Facts, asked straight from the phone ---

async function lookupBarcode(rawCode) {
  const code = String(rawCode).replace(/\s+/g, "");
  if (!validBarcode(code)) return { kind: "notfound" };  // never put anything else in the URL
  const url = `https://world.openfoodfacts.org/api/v2/product/${code}.json?fields=product_name,brands,proteins_100g,serving_size`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (response.status === 404) return { kind: "notfound" };
    if (!response.ok) return { kind: "error" };
    return interpretOffProduct(await response.json());
  } catch (error) {
    // fetch() rejects when there is no network (or after 8 s); a broken JSON body is a server problem
    return error instanceof SyntaxError ? { kind: "error" } : { kind: "offline" };
  } finally {
    clearTimeout(timer);
  }
}

async function scanFlow() {
  const canScan = "BarcodeDetector" in window && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  let stream = null;
  let timer = null;
  let code = null;
  let closed = false;

  const stopCamera = () => {
    clearInterval(timer);
    timer = null;
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
    const video = $("scan-video");
    if (video) video.srcObject = null;
  };

  const tryCode = () => {
    const typed = $("scan-code").value.replace(/\s+/g, "");
    if (!validBarcode(typed)) {
      $("scan-error").textContent = "Un code-barres, ce sont 8 à 14 chiffres.";
      $("scan-error").hidden = false;
      return;
    }
    code = typed;
    $("dialog").close();
  };

  const shown = showDialog(`
    <h2>Scanner un code-barres</h2>
    ${canScan
      ? `<div class="scan-box"><video id="scan-video" playsinline muted></video></div>
         <p id="scan-status" class="muted" role="status">Ouverture de la caméra…</p>`
      : `<p class="muted">Le scan avec la caméra n'est pas disponible ici : tape le code à la main.</p>`}
    <div class="form">
      <label>Ou tape le code<input id="scan-code" inputmode="numeric" autocomplete="off" maxlength="20" placeholder="ex. 3017620422003"></label>
      <p id="scan-error" class="form-error" role="alert" hidden></p>
      <button type="button" class="primary" id="scan-go">Chercher</button>
    </div>`,
    [{ label: "Fermer", value: null }],
  );
  $("scan-go").onclick = tryCode;
  $("scan-code").onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); tryCode(); } };

  const startCamera = async () => {
    const status = (text) => { const el = $("scan-status"); if (el) el.textContent = text; };
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
      if (closed) { stopCamera(); return; }  // the dialog was closed while the camera was starting
      const video = $("scan-video");
      video.srcObject = stream;
      await video.play();
      if (closed) { stopCamera(); return; }
      let detector;
      try { detector = new BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e"] }); }
      catch { detector = new BarcodeDetector(); }
      status("Place le code-barres dans le cadre.");
      let busy = false;
      timer = setInterval(async () => {
        if (busy || code || closed) return;
        busy = true;
        try {
          const hit = (await detector.detect(video)).find((b) => validBarcode(b.rawValue));
          // `closed` = the scan dialog is gone: never close some other dialog from here
          if (hit && !code && !closed) { code = hit.rawValue.replace(/\s+/g, ""); $("dialog").close(); }
        } catch { /* a frame that can't be read: try the next one */ }
        busy = false;
      }, 300);
    } catch {
      stopCamera();
      status("Caméra indisponible (autorisation refusée ?). Tape le code à la main.");
    }
  };
  if (canScan) startCamera();

  await shown;
  closed = true;
  stopCamera();  // always: the camera light must go off
  $("scan-go").onclick = null;
  $("scan-code").onkeydown = null;
  if (!code) return;

  showToast("Recherche du produit…", null, null, 9000);
  const result = await lookupBarcode(code);
  hideToast();

  if (result.kind === "ok") {
    const title = result.brand ? `${result.name} — ${result.brand}` : result.name;
    await askQuantity({
      name: title,
      per100: result.per100,
      defaultGrams: result.servingGrams,
      note: `${String(result.per100).replace(".", ",")} g de protéines / 100 g · Open Food Facts`
        + (result.servingGrams ? ` · portion indiquée : ${result.servingGrams} g` : ""),
    });
    return;
  }

  const messages = {
    offline: ["📴 Pas de connexion", "Je ne peux pas interroger Open Food Facts sans internet. Tu peux chercher dans la liste ou saisir les protéines à la main."],
    notfound: ["Produit introuvable", "Ce code n'est pas (encore) dans Open Food Facts. Pas grave : saisie libre ou liste d'aliments."],
    error: ["Open Food Facts ne répond pas bien", "Réessaie dans un moment, ou saisis les protéines à la main."],
    noprotein: ["Pas d'info protéines", `${result.name ?? "Ce produit"} n'a pas de valeur de protéines dans Open Food Facts. Tu peux la saisir à la main.`],
  };
  const [title, text] = messages[result.kind] || messages.error;
  const next = await showDialog(
    `<h2>${esc(title)}</h2><p class="muted">${esc(text)}</p>`,
    [{ label: "Fermer", value: null }, { label: "Saisie libre", value: "free", className: "primary" }],
  );
  if (next === "free") await freeEntry({ label: result.kind === "noprotein" ? result.name : "" });
}


// --- Wiring ---

function setupAssiette() {
  renderShortcuts();
  renderEatOut();
  renderMealPrep();

  $("tabbar").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-tab]");
    if (!button) return;
    showTab(button.dataset.tab);
    // Progrès and Réglages read the snapshot: make sure it is fresh when the PC answers
    if (button.dataset.tab === "progres" || button.dataset.tab === "reglages") refreshSnapshot();
  });
  $("a-shortcuts").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-i]");
    if (button) quickAdd(SHORTCUTS[Number(button.dataset.i)]);
  });
  $("a-journal").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-id]");
    if (button) editLine(button.dataset.id).catch(assietteError);
  });
  $("a-free").addEventListener("click", () => freeEntry().catch(assietteError));
  $("a-search").addEventListener("click", () => searchFlow().catch(assietteError));
  $("a-scan").addEventListener("click", () => scanFlow().catch(assietteError));
  $("a-mealprep-body").addEventListener("change", (event) => {
    if (event.target.matches("#a-shop input")) saveShopChecks();
  });
  $("a-mealprep-body").addEventListener("click", (event) => {
    if (!event.target.closest("#a-shop-reset")) return;
    document.querySelectorAll("#a-shop input").forEach((box) => { box.checked = false; });
    saveShopChecks();
  });

  // A workout left in progress: land on Aujourd'hui so "Reprendre" is visible, whatever the saved tab
  const saved = store.get("tab");
  showTab(loadLocalWorkout() ? "today" : TABS.includes(saved) ? saved : "today");
}
