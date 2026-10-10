// Salle & Assiette — static content of the "Assiette" screen (SPEC §4.7).
// Data only (plus tiny SVG builders): the screen logic lives in assiette.js.
// SVG colours come from CSS classes (.f-go, .f-warn … in style.css) so dark mode just works.


// --- 8 one-tap shortcuts (SPEC §4.7): grams = grams of PROTEIN ---

const SHORTCUTS = [
  { label: "Blanc de poulet", grams: 35, emoji: "🍗" },
  { label: "Steak", grams: 26, emoji: "🥩" },
  { label: "Thon", grams: 25, emoji: "🐟" },
  { label: "Skyr", grams: 15, emoji: "🥛" },
  { label: "Fromage blanc", grams: 16, emoji: "🥣" },
  { label: "3 œufs", grams: 18, emoji: "🥚" },
  { label: "Lentilles", grams: 18, emoji: "🫘" },
  { label: "Whey", grams: 24, emoji: "💪" },
];


// --- Built-in food list: INDICATIVE values (cooked / ready to eat, rounded averages).
// It is NOT an official table. per100 = g of protein per 100 g; portionG = a typical portion.

const FOODS = [
  // Viandes
  { name: "Blanc de poulet cuit", per100: 31, portionG: 120, portion: "1 blanc", alias: "volaille escalope" },
  { name: "Cuisse de poulet (sans peau)", per100: 26, portionG: 100, portion: "1 cuisse", alias: "volaille" },
  { name: "Escalope de dinde cuite", per100: 29, portionG: 120, portion: "1 escalope", alias: "volaille" },
  { name: "Steak haché 5 % cuit", per100: 26, portionG: 125, portion: "1 steak", alias: "boeuf viande hachee" },
  { name: "Bœuf cuit (bavette, rumsteck)", per100: 28, portionG: 130, portion: "1 pièce", alias: "steak viande" },
  { name: "Filet de porc cuit", per100: 27, portionG: 120, portion: "1 tranche épaisse" },
  { name: "Jambon blanc", per100: 21, portionG: 80, portion: "2 tranches" },
  // Poissons
  { name: "Thon au naturel (boîte, égoutté)", per100: 25, portionG: 110, portion: "1 petite boîte" },
  { name: "Saumon cuit", per100: 22, portionG: 130, portion: "1 pavé" },
  { name: "Cabillaud cuit", per100: 22, portionG: 140, portion: "1 pavé", alias: "poisson blanc colin" },
  { name: "Sardines en boîte", per100: 24, portionG: 90, portion: "1 boîte égouttée" },
  { name: "Crevettes cuites", per100: 22, portionG: 100, portion: "1 poignée" },
  { name: "Surimi", per100: 9, portionG: 80, portion: "4 bâtonnets" },
  // Œufs
  { name: "Œuf (dur, au plat, poché)", per100: 13, portionG: 55, portion: "1 œuf", alias: "oeuf" },
  { name: "Omelette (3 œufs)", per100: 11, portionG: 160, portion: "1 omelette", alias: "oeufs" },
  // Laitages
  { name: "Skyr nature", per100: 11, portionG: 140, portion: "1 pot" },
  { name: "Fromage blanc 0–3 %", per100: 8, portionG: 200, portion: "1 gros pot" },
  { name: "Cottage cheese", per100: 11, portionG: 125, portion: "1 pot", alias: "fromage frais" },
  { name: "Yaourt nature", per100: 4, portionG: 125, portion: "1 pot" },
  { name: "Yaourt grec", per100: 6, portionG: 150, portion: "1 pot" },
  { name: "Mozzarella", per100: 18, portionG: 125, portion: "1 boule" },
  { name: "Emmental / comté", per100: 28, portionG: 30, portion: "1 portion", alias: "fromage gruyere" },
  { name: "Lait demi-écrémé", per100: 3.3, portionG: 250, portion: "1 grand verre" },
  // Légumineuses
  { name: "Lentilles cuites", per100: 9, portionG: 200, portion: "1 assiette", alias: "legumineuses" },
  { name: "Pois chiches cuits", per100: 8, portionG: 150, portion: "1 portion", alias: "legumineuses" },
  { name: "Haricots rouges cuits", per100: 8, portionG: 150, portion: "1 portion", alias: "legumineuses" },
  { name: "Tofu ferme", per100: 14, portionG: 120, portion: "1/2 bloc", alias: "soja vegetarien" },
  { name: "Houmous", per100: 7, portionG: 50, portion: "2 c. à soupe" },
  // Féculents et divers
  { name: "Pâtes cuites", per100: 5.5, portionG: 200, portion: "1 assiette" },
  { name: "Riz cuit", per100: 2.7, portionG: 180, portion: "1 assiette" },
  { name: "Quinoa cuit", per100: 4.5, portionG: 180, portion: "1 assiette" },
  { name: "Pain (baguette)", per100: 9, portionG: 60, portion: "1/4 de baguette" },
  { name: "Flocons d'avoine", per100: 13, portionG: 50, portion: "1 bol" },
  { name: "Amandes", per100: 21, portionG: 30, portion: "1 poignée" },
  { name: "Cacahuètes", per100: 25, portionG: 30, portion: "1 poignée" },
  { name: "Whey (1 dose)", per100: 80, portionG: 30, portion: "1 dose", alias: "proteine shake poudre" },
  { name: "Barre protéinée", per100: 35, portionG: 50, portion: "1 barre" },
  // Sandwichs et plats courants
  { name: "Sandwich jambon-beurre", per100: 11, portionG: 190, portion: "1 sandwich", alias: "boulangerie" },
  { name: "Sandwich poulet crudités", per100: 10, portionG: 230, portion: "1 sandwich", alias: "boulangerie" },
  { name: "Cheeseburger", per100: 12, portionG: 150, portion: "1 burger", alias: "fast-food hamburger" },
  { name: "Kebab (sandwich)", per100: 11, portionG: 350, portion: "1 sandwich", alias: "grec" },
  { name: "Pizza (la moitié)", per100: 11, portionG: 200, portion: "1/2 pizza" },
  { name: "Quiche lorraine", per100: 10, portionG: 150, portion: "1 part" },
  { name: "Croque-monsieur", per100: 13, portionG: 150, portion: "1 croque" },
  { name: "Sushis assortis (8 pièces)", per100: 7, portionG: 220, portion: "8 pièces", alias: "maki japonais" },
  { name: "Steak-frites (viande seule)", per100: 26, portionG: 150, portion: "1 steak", alias: "brasserie boeuf" },
];


// --- "Manger dehors" guide: 80/20, no forbidden food, just good picks ---

const EAT_OUT = [
  {
    id: "brasserie", title: "Brasserie", emoji: "🍽️",
    picks: [
      "Pavé de bœuf ou de saumon grillé avec légumes et une portion de féculents (≈ 35 g de protéines)",
      "Steak-frites : la viande en entier, les frites partagées ou à moitié",
      "Salade composée avec poulet ou œuf, le pain à côté",
    ],
    tip: "Demande des légumes à la place d'une partie des frites. Et un dessert, ça passe très bien : c'est du 80/20.",
  },
  {
    id: "boulangerie", title: "Boulangerie", emoji: "🥖",
    picks: [
      "Sandwich poulet-crudités ou jambon, plutôt que le tout-fromage",
      "Quiche + petite salade (≈ 15 g de protéines)",
      "Formule sandwich + yaourt ou skyr si tu en trouves",
    ],
    tip: "Ajoute un œuf dur, un laitage ou une poignée d'amandes : +10 g de protéines sans effort.",
  },
  {
    id: "sushi", title: "Sushi", emoji: "🍣",
    picks: [
      "Sashimis ou sushis saumon/thon + edamame + soupe miso",
      "Chirashi ou poke bowl : poisson, riz, légumes",
      "Makis au saumon ou au thon, plutôt que les tempuras",
    ],
    tip: "Compte 10 à 12 pièces avec des edamames : ≈ 30 g de protéines. Pas besoin de refuser les makis au fromage, juste d'en prendre un peu moins.",
  },
  {
    id: "kebab", title: "Kebab", emoji: "🥙",
    picks: [
      "Sandwich poulet avec beaucoup de salade-tomate-oignon, sauce à côté",
      "Assiette viande + salade + un peu de frites (≈ 35 g de protéines)",
      "Dürüm ou galette : la viande fait le travail",
    ],
    tip: "La sauce blanche, tu la goûtes avant d'en mettre partout. Si tu craques pour les frites, partage-les.",
  },
  {
    id: "fastfood", title: "Fast-food", emoji: "🍔",
    picks: [
      "Burger double steak (plus de protéines qu'un simple), sans forcément le menu",
      "Wrap ou burger au poulet grillé plutôt que frit",
      "Salade + nuggets/poulet si tu veux plus léger, sans te forcer",
    ],
    tip: "Un burger de temps en temps, ça va : ajoute des crudités, bois de l'eau, et on n'en fait pas une histoire.",
  },
];

// The balanced plate: half vegetables, a quarter protein, a quarter starch.
function plateSvg() {
  return `<svg class="plate" viewBox="0 0 200 200" role="img" aria-label="Assiette équilibrée : la moitié en légumes, un quart de protéines, un quart de féculents">
    <circle cx="100" cy="100" r="96" class="f-surface s-line" stroke-width="3"/>
    <path d="M100 12 A88 88 0 0 0 100 188 Z" class="f-go"/>
    <path d="M100 100 L100 12 A88 88 0 0 1 188 100 Z" class="f-stop"/>
    <path d="M100 100 L188 100 A88 88 0 0 1 100 188 Z" class="f-warn"/>
    <circle cx="100" cy="100" r="88" fill="none" class="s-surface" stroke-width="3"/>
    <line x1="100" y1="12" x2="100" y2="188" class="s-surface" stroke-width="3"/>
    <line x1="100" y1="100" x2="188" y2="100" class="s-surface" stroke-width="3"/>
    <text x="52" y="108" text-anchor="middle" class="plate-text">½</text>
    <text x="146" y="60" text-anchor="middle" class="plate-text">¼</text>
    <text x="146" y="156" text-anchor="middle" class="plate-text">¼</text>
  </svg>`;
}

const PLATE_LEGEND = [
  { cls: "f-go", text: "½ légumes (crus, cuits, soupe)" },
  { cls: "f-stop", text: "¼ protéines (viande, poisson, œufs, légumineuses)" },
  { cls: "f-warn", text: "¼ féculents (riz, pâtes, pain, pommes de terre)" },
];


// --- Meal prep: 6 dishes, each with a code-drawn picture ---

// A bowl (viewBox 0 0 120 90) with the food drawn on top of it.
function bowlSvg(food) {
  return `<svg viewBox="0 0 120 90" aria-hidden="true" focusable="false">
    <ellipse cx="60" cy="80" rx="42" ry="5" class="f-ink" opacity="0.12"/>
    ${food}
    <path d="M10 44 H110 A50 38 0 0 1 10 44 Z" class="f-accent"/>
    <path d="M10 44 H110" class="s-surface" stroke-width="3" fill="none"/>
  </svg>`;
}

const MEAL_PREP = [
  {
    id: "poulet-riz", name: "Poulet, riz et brocolis", protein: 40,
    recipe: "Poulet en dés poêlé au paprika, riz basmati, brocolis vapeur. Cuis pour 4 : ça se garde 4 jours au frigo.",
    items: ["Blancs de poulet", "Riz basmati", "Brocolis", "Paprika"],
    svg: bowlSvg(`
      <ellipse cx="60" cy="42" rx="42" ry="12" class="f-surface s-line" stroke-width="1.5"/>
      <rect x="30" y="26" width="16" height="12" rx="4" class="f-warn"/>
      <rect x="49" y="22" width="16" height="12" rx="4" class="f-warn"/>
      <circle cx="78" cy="30" r="8" class="f-go"/><circle cx="90" cy="34" r="7" class="f-go"/><circle cx="70" cy="37" r="6" class="f-go"/>`),
  },
  {
    id: "chili", name: "Chili bœuf et haricots rouges", protein: 38,
    recipe: "Bœuf haché, oignon, tomates concassées, haricots rouges et cumin : 25 min de mijotage. Ça se congèle très bien.",
    items: ["Bœuf haché 5 %", "Haricots rouges (boîte)", "Tomates concassées (boîte)", "Oignons", "Riz basmati", "Cumin"],
    svg: bowlSvg(`
      <ellipse cx="60" cy="40" rx="42" ry="11" class="f-stop"/>
      <ellipse cx="42" cy="36" rx="5" ry="3" class="f-warn"/><ellipse cx="58" cy="32" rx="5" ry="3" class="f-warn"/>
      <ellipse cx="74" cy="37" rx="5" ry="3" class="f-warn"/><ellipse cx="88" cy="33" rx="5" ry="3" class="f-warn"/>
      <circle cx="62" cy="38" r="8" class="f-surface"/>`),
  },
  {
    id: "dahl", name: "Dahl de lentilles corail et œuf", protein: 28,
    recipe: "Lentilles corail, lait de coco léger, curry et oignon : 20 min. Un œuf dur par-dessus, quelques épinards si tu en as.",
    items: ["Lentilles corail", "Lait de coco léger", "Oignons", "Œufs", "Épinards", "Curry"],
    svg: bowlSvg(`
      <ellipse cx="60" cy="41" rx="42" ry="11" class="f-warn"/>
      <ellipse cx="50" cy="32" rx="13" ry="9" class="f-surface"/><circle cx="50" cy="32" r="5" class="f-warn"/>
      <ellipse cx="78" cy="35" rx="7" ry="3" class="f-go" transform="rotate(-20 78 35)"/>
      <ellipse cx="88" cy="40" rx="6" ry="3" class="f-go" transform="rotate(15 88 40)"/>`),
  },
  {
    id: "saumon", name: "Saumon, patate douce et haricots verts", protein: 34,
    recipe: "Pavés de saumon et dés de patate douce au four 20 min à 200 °C, haricots verts à la poêle avec un filet de citron.",
    items: ["Pavés de saumon", "Patates douces", "Haricots verts", "Citron"],
    svg: bowlSvg(`
      <rect x="22" y="26" width="38" height="16" rx="8" class="f-stop" transform="rotate(-6 41 34)"/>
      <rect x="66" y="28" width="12" height="12" rx="2" class="f-warn"/><rect x="80" y="31" width="12" height="12" rx="2" class="f-warn"/>
      <path d="M30 40 Q60 28 96 40" class="s-go" stroke-width="3" fill="none" stroke-linecap="round"/>
      <path d="M26 43 Q58 33 100 43" class="s-go" stroke-width="3" fill="none" stroke-linecap="round"/>`),
  },
  {
    id: "frittata", name: "Frittata légumes et feta", protein: 30,
    recipe: "4 œufs battus, courgette et poivron en dés, feta émiettée : poêle couverte 10 min. Se mange froide au bureau.",
    items: ["Œufs", "Courgettes", "Poivrons", "Feta"],
    svg: bowlSvg(`
      <ellipse cx="60" cy="40" rx="44" ry="13" class="f-warn"/>
      <circle cx="40" cy="38" r="4" class="f-go"/><circle cx="58" cy="33" r="4" class="f-stop"/>
      <circle cx="76" cy="40" r="4" class="f-go"/><circle cx="90" cy="35" r="4" class="f-stop"/>
      <rect x="48" y="40" width="7" height="6" rx="1" class="f-surface"/><rect x="66" y="31" width="7" height="6" rx="1" class="f-surface"/>`),
  },
  {
    id: "salade-thon", name: "Salade de pâtes au thon", protein: 36,
    recipe: "Pâtes complètes froides, thon au naturel, maïs, tomates et un trait d'huile d'olive-citron. Prête en 15 min, parfaite à emporter.",
    items: ["Pâtes complètes", "Thon au naturel (boîtes)", "Maïs (boîte)", "Salade verte", "Citron", "Huile d'olive"],
    svg: bowlSvg(`
      <ellipse cx="60" cy="40" rx="42" ry="11" class="f-go"/>
      <path d="M28 34 q6 -8 12 0 t12 0 t12 0" class="s-warn" stroke-width="4" fill="none" stroke-linecap="round"/>
      <path d="M44 42 q6 -8 12 0 t12 0 t12 0" class="s-warn" stroke-width="4" fill="none" stroke-linecap="round"/>
      <rect x="34" y="40" width="12" height="7" rx="3" class="f-stop" opacity="0.8"/><rect x="82" y="36" width="12" height="7" rx="3" class="f-stop" opacity="0.8"/>
      <circle cx="66" cy="31" r="2.5" class="f-warn"/><circle cx="73" cy="34" r="2.5" class="f-warn"/>`),
  },
];

// One shopping list for all the dishes: [{ key, name, dishes: ["Chili…", …] }], same item merged
// whatever the accents or the case. `normalize` is normalizeText from logic.js.
function shoppingList(dishes = MEAL_PREP) {
  const merged = new Map();
  for (const dish of dishes) {
    for (const item of dish.items) {
      const key = normalizeText(item);
      if (!merged.has(key)) merged.set(key, { key, name: item, dishes: [] });
      merged.get(key).dishes.push(dish.name);
    }
  }
  return [...merged.values()].sort((a, b) => a.name.localeCompare(b.name, "fr"));
}
