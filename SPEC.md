# Salle & Assiette — spécification technique

App perso de musculation + nutrition. Ce document décrit le prototype existant (HTML/JS mono-fichier, ~115 exercices, coach IA) et la cible à développer dans Claude Code (FastAPI + PostgreSQL + PWA).

- **Utilisateur** : une seule personne (Karim). Pas de multi-comptes, pas de Play Store.
- **Prototype actuel** : page HTML unique, état en `localStorage`, images en base64 inline (~3,5 Mo).
- **Problème à résoudre** : la régularité. L'app existe pour que je ne lâche pas, pas pour compter des chiffres.

---

## 1. Contexte et contraintes

| | |
|---|---|
| Objectif | Recomposition : ~88–90 kg → 78–80 kg en gardant/prenant du muscle (1,78–1,80 m) |
| Fréquence visée | 4 séances/semaine |
| Créneaux possibles | Matin tôt, pause déjeuner (2 h, salle à 15–20 min), après le travail |
| Ce qui m'a fait lâcher avant | Fatigue post-travail, flemme, alimentation trop stricte |
| Repères nutrition | ~2 150–2 350 kcal/j, 150–180 g de protéines/j, approche souple (80/20) |
| Repas | Souvent dehors près du bureau, motivé à cuisiner |

**Principes produit qui découlent de ça :**

1. **Autorégulation, pas discipline.** L'app adapte la séance à la forme du jour au lieu d'imposer un programme rigide.
2. **Jamais deux fois de suite.** Rater une séance est normal ; en rater deux d'affilée déclenche une relance.
3. **Pas de régime strict.** On suit les protéines, pas chaque calorie. Aucun aliment interdit, aucun message culpabilisant.
4. **Zéro friction.** Valider une série = 1 tap. Le check-in = 5 taps.
5. **Streak flexible** (X séances/semaine), jamais une chaîne quotidienne qui se casse.

---

## 2. Stack cible

| Couche | Choix | Note |
|---|---|---|
| Backend | FastAPI (Python) | API REST, logique d'autorégulation |
| DB | PostgreSQL (SQLite en dev) | |
| Front | PWA installable (écran d'accueil, offline) | Pas de store ; APK Capacitor plus tard si besoin |
| IA | API Anthropic (`claude-sonnet-*`), tool use | Remplace la capability `sample` du prototype |
| Notifications | Telegram bot (simple) ou ntfy (self-host) | Web Push VAPID en option |
| Planification | n8n ou APScheduler | Check-in du matin, relance, bilan hebdo |
| Exercices | Free Exercise DB (yuhonas) — **Unlicense, domaine public** | 800+ exos, JSON + photos, aucune attribution requise |
| Aliments | Open Food Facts (code-barres) + Ciqual ANSES | Ciqual : mention obligatoire « Anses. 2025. Table Ciqual » |

⚠️ Ne pas scraper les programmes payants (Nippard, RP) : sous copyright.

---

## 3. Modèle de données

### 3.1 État actuel du prototype (`localStorage`, clé `karim-salle-v1`)

```jsonc
{
  "program": {                      // séances, clé = id
    "HA": { "name": "Haut A", "focus": "Force",
            "ex": [ { "n": "Développé couché barre", "s": 4, "r": "5-6" } ] }
  },
  "order": ["HA", "BA", "HB", "BB"],   // rotation
  "goals": { "sessions": 4, "protein": 160, "targetWeight": 79,
             "objective": "recompo", "coach": true },
  "plan": [ { "j": "Lundi", "m": "Midi" } ],     // créneaux prévus
  "checkin": { "date": "2026-09-24",
               "res": { "sc": 14, "lvl": "orange", "fmt": "minimale" },
               "session": "HA" },
  "draft": {                                      // séance en cours
    "session": "HA", "name": "Haut A", "focus": "Force", "fmt": "complete",
    "date": "2026-09-24", "startedAt": 1758738000000, "cur": 0,
    "ex": [ { "n": "Squat", "r": "5-6",
              "sets": [ { "w": "80", "r": "6", "done": true } ] } ]
  },
  "logs": [ /* mêmes objets que draft, sans cur/done, après finish() */ ],
  "weights": [ { "date": "2026-09-24", "kg": 88.4 } ],
  "protein": { "2026-09-24": [ ["Blanc de poulet 150 g", 35] ] },
  "custom": { "Ma machine": { "g": "Dos", "m": ["Dos"], "s": [], "c": [],
                              "iso": 1, "lower": 0, "eq": "Machine", "a": [] } },
  "chat": { "training": [ { "role": "user", "content": "..." } ], "food": [] }
}
```

### 3.2 Schéma SQL cible

```sql
user_profile(id, name, height_cm, objective, sessions_goal, protein_goal_g,
             target_weight_kg, coach_enabled)

exercise(slug PK, name_fr, group, equipment, is_isolation, is_lower,
         is_cardio, cardio_unit, default_reps, image_start_url, image_end_url,
         is_custom, owner_id)
exercise_muscle(exercise_slug, muscle, role)          -- role: primary | secondary

session_template(id, name, focus, position)           -- focus: Force|Hypertrophie|Libre
session_exercise(session_id, position, exercise_slug, sets, reps)

checkin(id, date, energy, sleep, motivation, soreness, minutes,
        score, level, format, session_id)             -- level: vert|orange|rouge

workout(id, session_id, name, focus, format, date, started_at, finished_at)
workout_exercise(workout_id, position, exercise_slug, target_reps)
workout_set(workout_exercise_id, position, weight, reps, done)

weight_log(date, kg)
protein_log(id, date, label, grams)
chat_message(id, topic, role, content, created_at, changes_json, undo_json)
```

Migration depuis le prototype : le JSON ci-dessus est exportable tel quel (Réglages → Voir mes données) et sert de seed.

---

## 4. Fonctionnalités, écran par écran

### 4.1 Aujourd'hui

- **En-tête** : date, prénom, compteur de la semaine. Visuel « barre de musculation » : un disque rempli par séance faite, le nombre de disques suit l'objectif hebdo.
- **Semaine** : 7 pastilles L→D, pleines si séance faite, contour accentué sur aujourd'hui.
- **Message dynamique** (`message()`), par priorité :
  - jamais de séance → « Première séance à venir… »
  - ≥ 3 jours sans séance → relance « jamais deux fois de suite »
  - objectif hebdo atteint → félicitations
  - séance faite aujourd'hui → rappel protéines
  - sinon → `X/Y cette semaine. Prochaine : <nom>. Ton sac est prêt ?`
- **Carte héro**, 3 états :
  1. séance en cours → animation de l'exercice courant, barre de progression, bouton *Reprendre*
  2. check-in fait → bordure colorée selon le feu, liste des exercices, sélecteur de séance, *Commencer*
  3. rien → prochaine séance de la rotation + invitation au check-in
- **Check-in** : 4 questions à 3 boutons emoji (Énergie 🪫🙂⚡, Sommeil 😵🙂😴, Motivation 🥱🙂🔥, Courbatures 😌😬🤕) + temps dispo (30/45/60+ min).
- **Plan de la semaine** : pour chaque séance, un jour et un moment (intentions d'implémentation).
- **Règles anti-flemme** dépliables.

### 4.2 Séance

**Hors séance** : une carte par séance de la rotation (nom, type, muscles couverts, bande de miniatures animées), bouton *Modifier*, et 3 boutons de lancement (complète / minimale / express). En dessous : *Demander au coach quel exercice choisir* et les modèles de création.

**En séance (plein écran, barre de navigation masquée)** :

- Barre du haut : *Quitter* (sans perdre la séance), nom de la séance, chrono.
- Segments de progression cliquables (un par exercice ; vert = terminé, bleu = courant).
- Un exercice à la fois : titre, badges muscles + matériel, animation, schéma anatomique, consignes dépliables, conseil de progression.
- **Séries** : gros champs kg / reps + bouton **Valider**.
  - Validation à vide → reprend la dernière charge connue et le haut de la fourchette de reps.
  - La charge validée est recopiée sur la série suivante.
  - Re-tap = dévalider.
  - Nouveau record → confettis + toast (hors cardio).
- **Minuteur de repos automatique** après validation : 60 s isolation, 90 s hypertrophie, 150 s force, 60 s entre blocs cardio. Boutons −15 s / +15 s / Passer, vibration en fin.
- Actions par exercice : + / − série, remplacer, retirer, ajouter un exercice à la volée.
- Navigation Précédent / Suivant, *Terminer* / *Terminer maintenant*, *Abandonner*.

**Fin de séance** : modale récapitulative — séries, tonnage (ou minutes de cardio), durée, compteur hebdo, records battus. Trophée 🏆 si l'objectif de la semaine vient d'être atteint, confettis si record ou objectif.

### 4.3 Éditeur de séance

Nom, type (Force / Hypertrophie / Libre), et par exercice : séries (stepper), reps (champ libre), monter/descendre, remplacer, retirer. Ajouter un exercice. Supprimer la séance (historique conservé, minimum une séance).

Note affichée : en format minimal seuls les 3 premiers exercices sont gardés — l'ordre compte.

### 4.4 Choix d'exercice

- Recherche insensible aux accents, portant sur le nom, le groupe, le matériel, les muscles **et les alias** (« butterfly », « smith », « multipower », « lat pulldown », « poulie haute »…).
- Deux rangées de filtres : **muscle** (9 groupes + Perso) et **matériel** (🏋️ Poids libres, ⚙️ Machines, 🔗 Poulies, 🤸 Poids du corps).
- Chaque ligne : miniature animée + nom + badges. Tap sur la miniature → visionneuse.
- Création d'un exercice perso (nom + groupe, sans animation).

### 4.5 Visionneuse d'exercice

Déclenchée par tap sur n'importe quelle animation ou miniature, partout dans l'app.
Contenu : nom, badges, grande image en alternance départ/arrivée, boutons Départ / Arrivée / Pause, **schéma anatomique**, consignes, puis selon le contexte *Choisir cet exercice* et *Demander au coach comment le faire*.

### 4.6 Schéma anatomique

Deux silhouettes SVG (face + dos) dessinées en code, une zone par muscle.
Muscle principal en bleu plein, secondaires en bleu translucide, reste en gris. Légende à côté.
Zones : Trapèzes, Épaules, Pectoraux, Biceps, Triceps, Avant-bras, Abdos, Grand dorsal, Milieu du dos, Lombaires, Fessiers, Abducteurs, Adducteurs, Quadriceps, Ischio-jambiers, Mollets.
Cas particuliers : un exercice cardio met en avant ses muscles secondaires ; un groupe large est étendu (`Dos` → grand dorsal + milieu du dos ; `Jambes` → quadriceps + ischios + fessiers) ; si rien ne correspond, le schéma n'est pas affiché.

### 4.7 Assiette

- **Compteur de protéines du jour** : total, jauge, reste à manger converti en portions.
- 8 raccourcis (poulet 35 g, steak 26 g, thon 25 g, skyr 15 g, fromage blanc 16 g, 3 œufs 18 g, lentilles 18 g, whey 24 g) + saisie libre.
- **Journal du jour modifiable** : chaque ligne peut être renommée, recorrigée en grammes (0 = suppression) ou supprimée.
- **Coach repas** : je décris ce que je mange → avis + conseil pour la prochaine fois + estimation protéines/kcal + bouton pour ajouter les protéines estimées.
- Guide « manger dehors » (brasserie, boulangerie, sushi, kebab, fast-food) avec schéma de l'assiette équilibrée (½ légumes, ¼ protéines, ¼ féculents).
- Meal prep : 6 plats illustrés en SVG avec leurs protéines + liste de courses.

### 4.8 Progrès

Séances totales, semaines tenues d'affilée, poids (courbe + ligne d'objectif + écart restant), **minutes de cardio de la semaine** (repère 150 min), records de charge par exercice, 6 dernières séances.

### 4.9 Réglages

Objectif principal (recompo / muscle / perte / forme, chacun avec son repère calorique), séances par semaine, protéines par jour (+ calcul auto depuis le poids : 2 g/kg en recompo et perte, 1,8 en prise, 1,6 en forme), poids visé, activation du coach, ordre de rotation, exercices perso, export JSON, remise à zéro.

---

## 5. Algorithmes

### 5.1 Readiness (feu tricolore)

```
score = énergie + sommeil + motivation + (6 − courbatures)     // 4 à 20
niveau = score ≥ 15 → vert | score ≥ 10 → orange | sinon rouge

format = rouge                      → express
       = vert et temps ≥ 60 min     → complète
       = sinon                      → minimale
si temps ≤ 30 min                   → express (prioritaire)
```

### 5.2 Format de séance

| Format | Contenu | Durée |
|---|---|---|
| complète | tous les exercices | ≈ 60 min |
| minimale | 3 premiers exercices, max 3 séries | ≈ 35–40 min |
| express | 2 premiers exercices, max 3 séries | ≈ 25 min |

C'est la règle anti-flemme : un feu rouge ne dit jamais « reste chez toi », il dit « fais la version express ».

### 5.3 Surcharge progressive

Recherche de la dernière performance sur l'exercice, puis :

- jamais fait → « prends une charge où il te reste 2 à 3 reps en réserve »
- toutes les séries au haut de la fourchette → proposer **+1 kg** (isolation), **+2,5 kg** (haut du corps), **+5 kg** (bas du corps)
- sinon → même charge, une répétition de plus
- **cardio** → cumul des minutes de la dernière fois, puis « ajoute 2 à 5 min ou monte un peu le niveau / la vitesse / la distance »

### 5.4 Cardio

13 exercices en durée (reps = minutes, séries = blocs) avec une unité propre (km/h, niveau, mètres, km, tours, kg). Exclus des records de charge et du tonnage ; comptés en minutes. 6 exercices HIIT (mountain climbers, jumping jacks, squat sauté, kettlebell swing, battle ropes, box jump) restent en répétitions.

### 5.5 Semaines tenues

Compte les semaines consécutives (en remontant) où le nombre de séances ≥ `max(2, objectif − 1)`. Tolérant d'une séance : c'est un streak flexible.

---

## 6. Bibliothèque d'exercices

**115 exercices**, chacun avec 2 photos (départ / arrivée) alternées pour simuler un GIF, muscles principaux et secondaires en français, 3 consignes d'exécution, matériel et alias de recherche.

| Groupe | Nb | | Matériel | Nb |
|---|---|---|---|---|
| Jambes | 20 | | Poids libre | 42 |
| Cardio | 19 | | Machine | 40 |
| Dos | 16 | | Poulie | 25 |
| Pectoraux | 15 | | Poids du corps | 8 |
| Épaules | 13 | | | |
| Biceps / Triceps / Abdos | 9 chacun | | | |
| Avant-bras | 5 | | | |

Structure d'une entrée :

```jsonc
"Pec deck (butterfly)": {
  "g": "Pectoraux",          // groupe
  "m": ["Pectoraux"],        // muscles principaux
  "s": [],                   // muscles secondaires
  "c": ["Dos collé au dossier", "..."],   // consignes
  "img": ["data:image/jpeg;base64,...", "..."],
  "eq": "Machine",           // Poids libre | Machine | Poulie | Poids du corps
  "a": ["butterfly", "papillon", "pec fly"],   // alias de recherche
  "iso": 1, "lower": 0,      // isolation ? bas du corps ?
  "cardio": 1, "unit": "km/h", "r": "20"   // cardio seulement
}
```

Source : `github.com/yuhonas/free-exercise-db` (Unlicense). Les images sont redimensionnées à 360 px, JPEG qualité 58. **En prod, les servir comme fichiers statiques et non en base64** — c'est ce qui rend le prototype si lourd.

### Programme par défaut (Haut/Bas 4 jours)

- **Haut A — Force** : développé couché 4×5-6, rowing barre 4×6-8, développé militaire 3×6-10, tirage vertical 3×8-10, curl haltères 2×10-12, extension triceps poulie 2×10-12
- **Bas A — Force** : squat 4×5-6, SDT roumain 3×6-8, presse à cuisses 3×10-12, leg curl 3×10-12, mollets 4×10-15
- **Haut B — Hypertrophie** : développé incliné haltères 4×8-12, tirage horizontal 4×10-12, élévations latérales 4×12-15, développé prise serrée 3×10-12, curl incliné 3×12-15, face pull 3×15-20
- **Bas B — Hypertrophie** : squat bulgare 3×10-12, hip thrust 3×10-12, leg extension 3×12-15, leg curl assis 3×12-15, mollets debout 4×12-20

Modèles de création fournis : séance vide, « Pecs · épaules · triceps », « Dos · biceps · avant-bras », « Jambes · abdos », « Cardio · gainage », « Full body ».

---

## 7. Coach IA

Rien n'est écrit à l'avance : tous les conseils sont générés. Trois points d'entrée.

### 7.1 Conseil à l'ajout d'un exercice

Se déclenche quand j'ajoute ou remplace un exercice (programme ou séance en cours). Reçoit : profil, programme complet, muscles de l'exercice, contexte d'ajout, bibliothèque, ids de séances valides. Réponse en JSON :

```json
{ "verdict": "bien | mieux_ailleurs | meilleure_option",
  "message": "1 à 2 phrases : valoriser l'initiative, puis le conseil",
  "move_to": "id de séance ou null",
  "swap_for": "nom exact de la bibliothèque ou null",
  "order_tip": "conseil sur la place dans la séance, ou null" }
```

L'app valide `move_to` et `swap_for` avant d'afficher les boutons d'action (déplacer / remplacer / garder).

### 7.2 Avis sur un repas

Entrée libre. Réponse JSON : `score` (top / correct / a_ameliorer), `message`, `tip`, `protein_g`, `kcal`, `rest_of_day`. Bouton pour ajouter les protéines estimées au compteur.

Consigne de ton : jamais culpabilisant, pas de régime strict, valoriser d'abord puis un seul conseil concret et réaliste.

### 7.3 Chat, deux fils séparés

**Entraînement** et **Nutrition**, chacun avec son historique (40 derniers messages, 14 envoyés au modèle) et ses questions suggérées.

Contexte injecté dans le premier tour : profil et objectifs, programme complet, bibliothèque par matériel, **5 dernières séances avec charges et reps**, pesées récentes, feu du jour, protéines du jour, compteur hebdo, date et heure.

Réponse en streaming avec bouton Stop.

### 7.4 Outils (tool use) — le coach modifie l'app

| Outil | Rôle |
|---|---|
| `edit_session_exercise` | add / remove / replace / update / move sur un exercice d'une séance |
| `create_session` | crée une séance et l'ajoute à la rotation |
| `update_session` | renomme, change le type, ou supprime |
| `set_rotation_order` | réordonne la rotation |
| `create_custom_exercise` | crée un exercice perso |
| `update_goals` | objectif, séances/semaine, protéines, poids visé |
| `log_today` | protéines mangées et/ou pesée du jour |

**Garde-fous, à reprendre tels quels :**

- Le coach ne modifie rien sans demande explicite ou accord clair (« oui », « vas-y »). Sinon il propose et attend.
- Résolution tolérante des noms (`findEx` / `findSession`) : en cas d'échec, l'erreur **renvoie les noms proches** pour que le modèle se corrige tout seul au tour suivant.
- Chaque modification s'affiche sous la réponse avec un ✓.
- **Snapshot avant chaque tour** → bouton *Annuler ces changements* qui restaure l'état complet.
- Bornes systématiques : séries 1–10, protéines 60–300 g, séances 1–7, poids 30–250 kg, minimum une séance dans la rotation.
- Les modifications passées sont réinjectées dans l'historique (`[Modifications appliquées : …]`) pour que le modèle sache ce qu'il a déjà fait.

### 7.5 À porter côté backend

Le prototype utilise la capability `sample` de claude.ai. En autonome : endpoint FastAPI qui appelle l'API Anthropic, les outils devenant des fonctions serveur qui écrivent en base. Le format tool use est le même, la logique de garde-fous se transpose telle quelle.

---

## 8. Design

| Token | Clair | Sombre |
|---|---|---|
| `--bg` | `#E6EAEE` | `#16202B` |
| `--surface` | `#F8F9FB` | `#1F2B38` |
| `--ink` | `#17222D` | `#E7ECF1` |
| `--muted` | `#586675` | `#9AA8B6` |
| `--line` | `#CAD2DB` | `#324252` |
| `--accent` | `#2B54D6` | `#7393F5` |
| `--go` / `--warn` / `--stop` | `#2E8B57` / `#B97C0C` / `#C23F38` | `#4DB27A` / `#E3AE45` / `#E06A62` |

- Typo : **Barlow Condensed** (titres, chiffres) + **Figtree** (texte).
- Mode sombre automatique via `prefers-color-scheme`, surchargeable par `data-theme`.
- Cibles tactiles ≥ 46 px, ≥ 52 px pendant la séance (utilisable d'une main à la salle).
- `prefers-reduced-motion` respecté partout (animations, confettis, minuteur).
- Largeur max 560 px, testé jusqu'à 360 px sans scroll horizontal.
- **Pas de `confirm()` / `alert()`** : boîte de dialogue maison (bloquée dans une iframe, c'était un vrai bug).

---

## 9. Historique des modifications demandées

Dans l'ordre où je les ai demandées, chacune corrigeant un manque réel :

1. **Images d'exercices** — mini-GIF départ/arrivée + consignes en français, parce que les noms seuls ne suffisent pas pour savoir quoi faire.
2. **Muscles ciblés + tout rendre modifiable** — badges muscles ; édition complète des séances (ajout, retrait, remplacement, réordonnancement, séries, reps) ; création de séances depuis modèles ; bibliothèque avec recherche et filtres ; exercices perso ; objectifs modifiables dans un onglet Réglages.
3. **Correctif boutons** — *Commencer* et *Abandonner* ne réagissaient pas : `confirm()` est bloqué dans le cadre d'affichage. Remplacé par une modale interne.
4. **Coach IA** — conseils générés sur les ajouts d'exercices et sur les repas.
5. **Chat coach** — deux fils (entraînement / nutrition) avec le contexte réel de l'app.
6. **Coach qui agit + zoom exercice** — tool use avec annulation ; visionneuse plein écran au tap sur une image.
7. **Refonte visuelle** — carte héro, check-in en emoji, mode séance plein écran, minuteur de repos automatique, célébrations et confettis, mode sombre soigné.
8. **Machines et poulies** — +41 exercices (butterfly, presses, Smith, hack squat, adducteurs…), filtre par matériel, alias de salle.
9. **Cardio** — +19 exercices notés en minutes, unités propres, minutes de cardio dans les stats, modèle « Cardio · gainage ».
10. **Protéines corrigeables** — journal du jour éditable et supprimable (avant, une erreur de saisie était définitive).
11. **Schéma anatomique** — silhouettes face/dos avec les zones travaillées coloriées.

---

## 10. Roadmap

**Phase 1 — socle (2–3 week-ends)**
FastAPI + DB, import du JSON du prototype, endpoints check-in / séance / log, PWA avec les écrans Aujourd'hui + Séance, images servies en statique.

**Phase 2 — nutrition**
Protéines, Open Food Facts (code-barres), Ciqual, journal éditable.

**Phase 3 — coach**
Endpoint `/coach/chat` vers l'API Anthropic avec les 7 outils, conseil à l'ajout d'exercice, avis repas.

**Phase 4 — notifications**
Bot Telegram ou ntfy. **Une seule notification par jour**, personnalisée et envoyée au bon moment (matin d'un jour d'entraînement prévu, ou après 2 jours d'inactivité). Au-delà, c'est de la fatigue de notification et je les coupe.

**Phase 5 — automatismes**
Health Connect (sommeil, pas) pour pré-remplir le check-in ; bilan hebdo du dimanche avec ajustement des calories selon la courbe de poids.

---

## 11. Pièges repérés

- **Poids du prototype** : les images en base64 font 3,4 Mo sur 3,5. Premier chantier : fichiers statiques + `<picture>`/lazy loading.
- **`localStorage` peut échouer** (navigation privée, stockage bloqué) : tous les accès sont déjà en `try/catch`, garder ce réflexe.
- **Migrations** : le prototype a déjà eu besoin de renommer un exercice dans l'historique (`Pec deck` → `Pec deck (butterfly)`). Prévoir de vraies migrations côté DB, l'historique ne doit jamais casser.
- **Ne pas sur-concevoir.** Chaque week-end de code doit sortir une fonctionnalité utilisable ; sinon je procrastine l'entraînement en codant l'app censée m'y amener.
- **Commencer à s'entraîner avant que l'app soit prête.** Le prototype suffit pour logger dès maintenant.

---

*Information générale, pas un avis médical. Les repères caloriques sont des estimations à ajuster sur 2 à 4 semaines de données réelles.*
